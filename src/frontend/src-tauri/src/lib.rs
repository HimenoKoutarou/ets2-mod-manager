#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
#![cfg_attr(not(feature = "desktop"), allow(dead_code))]

use aes::cipher::{BlockDecrypt, BlockEncrypt, KeyInit};
use aes::{cipher::generic_array::GenericArray, Aes256};
use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use flate2::{read::ZlibDecoder, write::ZlibEncoder, Compression};
use getrandom::fill as fill_random;
use regex::Regex;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use sha1::{Digest, Sha1};
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::{
    collections::{HashMap, HashSet},
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    process::Stdio,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex, OnceLock,
    },
    time::{SystemTime, UNIX_EPOCH},
};
#[cfg(feature = "desktop")]
use tauri::{AppHandle, Emitter, State};

#[cfg(not(feature = "desktop"))]
type State<'a, T> = &'a T;
use zip::ZipArchive;
mod categories;
mod mod_directory;

#[cfg(windows)]
use winreg::{enums::HKEY_CURRENT_USER, RegKey};

const SCSC_KEY: [u8; 32] = [
    0x2A, 0x5F, 0xCB, 0x17, 0x91, 0xD2, 0x2F, 0xB6, 0x02, 0x45, 0xB3, 0xD8, 0x36, 0x9E, 0xD0, 0xB2,
    0xC2, 0x73, 0x71, 0x56, 0x3F, 0xBF, 0x1F, 0x3C, 0x9E, 0xDF, 0x6B, 0x11, 0x82, 0x5A, 0x5D, 0x0A,
];

#[derive(Clone)]
struct BackendState {
    inner: Arc<Mutex<Backend>>,
}

struct Backend {
    paths: Paths,
    database_path: PathBuf,
    scan_cancelled: Arc<AtomicBool>,
    localization_cancelled: Arc<AtomicBool>,
}

#[derive(Clone, Debug)]
struct Paths {
    game_root: PathBuf,
    mod_root: PathBuf,
    profiles_root: PathBuf,
    steam_profiles_root: Option<PathBuf>,
    cloud_profiles_root: Option<PathBuf>,
    workshop_roots: Vec<PathBuf>,
    game_executable: Option<PathBuf>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ProfileDto {
    id: String,
    name: String,
    company: String,
    location: String,
    folder: String,
    mod_count: usize,
    writable: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ModDto {
    id: String,
    package_name: String,
    path: String,
    package_type: String,
    display_name: String,
    author: String,
    version: String,
    size: u64,
    modified_ms: i64,
    enabled: bool,
    category: String,
    #[serde(skip)]
    fingerprint: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ModMediaRequest {
    mod_id: String,
    path: String,
    package_type: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ModMediaDto {
    mod_id: String,
    icon_url: Option<String>,
    preview_url: Option<String>,
}

#[derive(Clone, Debug, Default, Deserialize)]
struct WorkshopCacheEntry {
    #[serde(default)]
    title: String,
    #[serde(default)]
    preview_url: String,
}

static WORKSHOP_CACHE: OnceLock<HashMap<String, WorkshopCacheEntry>> = OnceLock::new();
static DB_WRITE_LOCK: OnceLock<Mutex<()>> = OnceLock::new();
const EMBEDDED_WORKSHOP_TITLES: &str =
    include_str!("../../../../assets/cache/workshop_titles.json");

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct PresetDto {
    name: String,
    active_mods: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ScanSummary {
    total: usize,
    added: usize,
    updated: usize,
    removed: usize,
    inspected: usize,
    elapsed_ms: u128,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ScanProgress {
    phase: String,
    current: usize,
    total: usize,
    name: String,
    path: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SaveResult {
    success: bool,
    message: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WriteActiveRequest {
    profile_id: String,
    active_mods: Vec<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct PresetRequest {
    profile_id: String,
    name: String,
    active_mods: Option<Vec<String>>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct MoveRequest {
    profile_id: String,
    active_mods: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SaveSlotDto {
    profile_id: String,
    slot_id: String,
    folder: String,
    game_sii: String,
    display_name: String,
    last_modified_ms: i64,
    profile_location: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct LocalizationEntryDto {
    key: String,
    value: String,
    source_name: String,
    source_path: String,
    package_name: String,
    category: String,
    status: String,
    locale_key_present: bool,
    def_locale_key_present: bool,
    unit_name: String,
    locale_key: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct LocalizationScanDto {
    entries: Vec<LocalizationEntryDto>,
    packages: usize,
    inspected: usize,
    cached: usize,
    elapsed_ms: u128,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct LocalizationScanRequest {
    profile_id: String,
    target_locale: Option<String>,
    base_file: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct LocalizationWriteRequest {
    base_file: String,
    entries: Vec<LocalizationEntryDto>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CrashPrecheckRequest {
    profile_id: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CrashPairDto {
    crash_path: Option<String>,
    log_path: Option<String>,
    source: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CrashIssueDto {
    mod_id: String,
    display_name: String,
    severity: String,
    code: String,
    evidence: String,
    priority_index: Option<usize>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CrashPrecheckDto {
    profile_id: String,
    scanned_mods: usize,
    red_count: usize,
    yellow_count: usize,
    issues: Vec<CrashIssueDto>,
    log_path: Option<String>,
    crash_path: Option<String>,
    log_summary: String,
    log_evidence: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct BsiiSummaryDto {
    version: u32,
    definitions: u32,
    objects: u32,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SaveFieldDto {
    structure_name: String,
    field_name: String,
    type_id: u32,
    value: i64,
    offset: usize,
    size: usize,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SaveSnapshotDto {
    version: u32,
    fields: Vec<SaveFieldDto>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SaveObjectFieldDto {
    name: String,
    type_id: u32,
    value: String,
    offset: usize,
    size: usize,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SaveObjectDto {
    object_index: usize,
    structure_name: String,
    kind: String,
    fields: Vec<SaveObjectFieldDto>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SaveInventoryDto {
    version: u32,
    objects: Vec<SaveObjectDto>,
    trucks: usize,
    trailers: usize,
    garages: usize,
    cities: usize,
    dealers: usize,
    skills: usize,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SaveMutationRequest {
    path: String,
    operation: String,
    value: i64,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SaveObjectMutationRequest {
    path: String,
    structure_name: String,
    object_index: usize,
    field_name: String,
    value: i64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SaveMutationDto {
    success: bool,
    operation: String,
    message: String,
    backup_path: Option<String>,
    value: Option<i64>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct BsiiInspectRequest {
    path: String,
}

impl Paths {
    fn detect() -> Self {
        let game_root = detect_game_root();
        let profiles_root = game_root.join("profiles");
        let steam_profiles = game_root.join("steam_profiles");
        let steam_profiles_root = steam_profiles.is_dir().then_some(steam_profiles);
        let steam_roots = discover_steam_roots();
        let workshop_roots = steam_roots
            .iter()
            .map(|root| root.join("steamapps/workshop/content/227300"))
            .filter(|candidate| candidate.is_dir())
            .collect();
        let cloud_profiles_root = find_cloud_profiles(&steam_roots);
        let game_executable = find_game_executable(&steam_roots);
        Self {
            mod_root: game_root.join("mod"),
            game_root,
            profiles_root,
            steam_profiles_root,
            cloud_profiles_root,
            workshop_roots,
            game_executable,
        }
    }
}

fn detect_game_root() -> PathBuf {
    let mut documents_roots = Vec::new();
    if let Some(user_profile) = std::env::var_os("USERPROFILE").map(PathBuf::from) {
        documents_roots.push(user_profile.join("Documents"));
        documents_roots.push(user_profile.join("OneDrive").join("Documents"));
        documents_roots.push(user_profile.join("我的文档"));
    }
    for variable in ["OneDrive", "OneDriveConsumer"] {
        if let Some(root) = std::env::var_os(variable).map(PathBuf::from) {
            documents_roots.push(root.join("Documents"));
            documents_roots.push(root);
        }
    }
    documents_roots.dedup();
    documents_roots
        .iter()
        .map(|root| root.join("Euro Truck Simulator 2"))
        .find(|candidate| candidate.is_dir())
        .or_else(|| {
            documents_roots
                .first()
                .map(|root| root.join("Euro Truck Simulator 2"))
        })
        .unwrap_or_else(|| PathBuf::from("Euro Truck Simulator 2"))
}

fn discover_steam_roots() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    #[cfg(windows)]
    {
        for key_path in [r"Software\Valve\Steam", r"Software\Wow6432Node\Valve\Steam"] {
            if let Ok(key) = RegKey::predef(HKEY_CURRENT_USER).open_subkey(key_path) {
                for value_name in ["SteamPath", "InstallPath"] {
                    if let Ok(value) = key.get_value::<String, _>(value_name) {
                        candidates.push(PathBuf::from(value));
                    }
                }
            }
        }
    }
    for drive in ["C:\\", "D:\\", "E:\\", "F:\\", "G:\\"] {
        let root = PathBuf::from(drive);
        candidates.extend([
            root.join("Program Files (x86)").join("Steam"),
            root.join("Program Files").join("Steam"),
            root.join("Steam"),
            root.join("SteamLibrary"),
        ]);
    }
    let initial = candidates.clone();
    for parent in initial {
        let vdf = parent.join("steamapps").join("libraryfolders.vdf");
        let Ok(text) = fs::read_to_string(vdf) else {
            continue;
        };
        candidates.extend(parse_steam_library_paths(&text));
    }
    let mut result = Vec::new();
    let mut seen = HashSet::new();
    for candidate in candidates {
        let candidate = fs::canonicalize(&candidate).unwrap_or(candidate);
        if candidate.is_dir() && seen.insert(normalize_path(&candidate).to_ascii_lowercase()) {
            result.push(candidate);
        }
    }
    result
}

fn parse_steam_library_paths(text: &str) -> Vec<PathBuf> {
    let Ok(pattern) = Regex::new(r#"(?i)"path"\s*"((?:\\.|[^"])*)""#) else {
        return Vec::new();
    };
    pattern
        .captures_iter(text)
        .filter_map(|capture| {
            let raw = capture.get(1)?.as_str();
            let mut value = String::with_capacity(raw.len());
            let mut chars = raw.chars();
            while let Some(ch) = chars.next() {
                if ch == '\\' {
                    if let Some(next) = chars.next() {
                        value.push(next);
                    }
                } else {
                    value.push(ch);
                }
            }
            (!value.trim().is_empty()).then(|| PathBuf::from(value))
        })
        .collect()
}

fn find_game_executable(steam_roots: &[PathBuf]) -> Option<PathBuf> {
    for root in steam_roots {
        let common = root.join("steamapps/common");
        for (directory, executable) in [
            ("Euro Truck Simulator 2", "eurotrucks2.exe"),
            ("American Truck Simulator", "amtrucks.exe"),
        ] {
            for architecture in ["win_x64", "win_x86"] {
                let candidate = common
                    .join(directory)
                    .join("bin")
                    .join(architecture)
                    .join(executable);
                if candidate.is_file() {
                    return Some(candidate);
                }
            }
        }
    }
    None
}

fn find_cloud_profiles(steam_roots: &[PathBuf]) -> Option<PathBuf> {
    for root in steam_roots {
        let userdata = root.join("userdata");
        let Ok(users) = fs::read_dir(userdata) else {
            continue;
        };
        for user in users.flatten() {
            let candidate = user.path().join("227300/remote/profiles");
            if candidate.is_dir() {
                return Some(candidate);
            }
        }
    }
    None
}

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|v| v.as_millis() as i64)
        .unwrap_or_default()
}

static LOG_FILE: OnceLock<Mutex<std::fs::File>> = OnceLock::new();

fn app_log(level: &str, message: &str) {
    let file = LOG_FILE.get_or_init(|| {
        let log_path = detect_game_root().join("ets2modmanager.log");
        let f = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .or_else(|_| {
                std::fs::OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open("ets2modmanager.log")
            })
            .expect("open log file");
        Mutex::new(f)
    });
    if let Ok(mut f) = file.lock() {
        let _ = writeln!(f, "[{}] [{}] {}", now_ms(), level, message);
    }
}

fn normalize_path(path: &Path) -> String {
    fs::canonicalize(path)
        .unwrap_or_else(|_| path.to_path_buf())
        .to_string_lossy()
        .replace('/', "\\")
        .trim_end_matches('\\')
        .to_string()
}

fn cache_directory() -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(root) = std::env::var_os("ETS2MM_ROOT").map(PathBuf::from) {
        candidates.push(root);
    }
    if let Ok(exe) = std::env::current_exe() {
        candidates.extend(exe.ancestors().map(Path::to_path_buf));
    }
    if let Ok(current) = std::env::current_dir() {
        candidates.extend(current.ancestors().map(Path::to_path_buf));
    }
    let mut seen = HashSet::new();
    candidates
        .into_iter()
        .map(|root| root.join("assets").join("cache"))
        .find(|candidate| {
            seen.insert(normalize_path(candidate).to_ascii_lowercase()) && candidate.is_dir()
        })
}

fn workshop_cache_entries() -> &'static HashMap<String, WorkshopCacheEntry> {
    WORKSHOP_CACHE.get_or_init(|| {
        let mut entries =
            serde_json::from_str::<HashMap<String, WorkshopCacheEntry>>(EMBEDDED_WORKSHOP_TITLES)
                .unwrap_or_default();
        if let Some(path) = cache_directory().map(|dir| dir.join("workshop_titles.json")) {
            if let Ok(text) = fs::read_to_string(path) {
                if let Ok(external) =
                    serde_json::from_str::<HashMap<String, WorkshopCacheEntry>>(&text)
                {
                    entries.extend(external);
                }
            }
        }
        entries
    })
}

fn workshop_cached_title(workshop_id: &str) -> Option<String> {
    if workshop_id.is_empty() || !workshop_id.chars().all(|value| value.is_ascii_digit()) {
        return None;
    }
    workshop_cache_entries()
        .get(workshop_id)
        .map(|entry| entry.title.trim().to_string())
        .filter(|value| !value.is_empty() && !value.chars().all(|c| c.is_ascii_digit()))
}

fn workshop_id_from_value(value: &str) -> Option<String> {
    let raw = value.split('|').next()?.trim();
    let suffix = raw.strip_prefix("mod_workshop_package.")?;
    if suffix.chars().all(|c| c.is_ascii_digit()) {
        return Some(suffix.to_string());
    }
    if suffix.chars().all(|c| c.is_ascii_hexdigit()) {
        return u64::from_str_radix(suffix, 16)
            .ok()
            .map(|id| id.to_string());
    }
    None
}

fn workshop_label_from_value(value: &str) -> Option<String> {
    let label = value.split_once('|')?.1.trim();
    if label.is_empty() || label.chars().all(|c| c.is_ascii_hexdigit()) {
        None
    } else {
        Some(label.to_string())
    }
}

fn workshop_cached_preview_url(workshop_id: &str) -> Option<String> {
    workshop_cache_entries()
        .get(workshop_id)
        .map(|entry| entry.preview_url.trim().to_string())
        .filter(|value| !value.is_empty())
}

fn workshop_log_title(workshop_id: &str, paths: &Paths) -> Option<String> {
    if workshop_id.is_empty() || !workshop_id.chars().all(|value| value.is_ascii_digit()) {
        return None;
    }
    let pattern =
        Regex::new(r"(?m)Active workshop mod ID\s+(\d+)\s+\(name:\s*(.*?),\s+version:").ok()?;
    for log_name in ["editor.log.txt", "game.log.txt"] {
        let path = paths.game_root.join(log_name);
        let Ok(text) = fs::read_to_string(path) else {
            continue;
        };
        for captures in pattern.captures_iter(&text) {
            if captures
                .get(1)
                .is_some_and(|value| value.as_str() == workshop_id)
            {
                let title = captures.get(2)?.as_str().trim();
                if !title.is_empty() {
                    return Some(title.to_string());
                }
            }
        }
    }
    None
}

fn profile_sii(folder: &Path) -> PathBuf {
    folder.join("profile.sii")
}

fn decode_scsc_or_plain(bytes: &[u8]) -> Result<Vec<u8>, String> {
    if bytes.len() < 4 || &bytes[..4] != b"ScsC" {
        return Ok(bytes.to_vec());
    }
    if bytes.len() < 56 {
        return Err("ScsC header is incomplete".into());
    }
    let iv: [u8; 16] = bytes[36..52]
        .try_into()
        .map_err(|_| "invalid ScsC IV".to_string())?;
    let expected_size = u32::from_le_bytes(
        bytes[52..56]
            .try_into()
            .map_err(|_| "invalid ScsC size".to_string())?,
    ) as usize;
    let decrypted = cbc_decrypt(&bytes[56..], &iv)?;
    let pad = *decrypted.last().ok_or("ScsC payload is empty")? as usize;
    if pad == 0
        || pad > 16
        || pad > decrypted.len()
        || decrypted[decrypted.len() - pad..]
            .iter()
            .any(|x| *x as usize != pad)
    {
        return Err("ScsC padding is invalid".into());
    }
    let decrypted = &decrypted[..decrypted.len() - pad];
    let mut decoder = ZlibDecoder::new(decrypted);
    let mut output = Vec::new();
    decoder
        .read_to_end(&mut output)
        .map_err(|e| format!("ScsC decompress failed: {e}"))?;
    if expected_size != 0 && output.len() != expected_size {
        return Err(format!(
            "ScsC decompressed size mismatch: {} != {}",
            output.len(),
            expected_size
        ));
    }
    Ok(output)
}

fn encode_scsc(plain: &[u8]) -> Result<Vec<u8>, String> {
    let mut compressed = Vec::new();
    {
        let mut encoder = ZlibEncoder::new(&mut compressed, Compression::best());
        encoder
            .write_all(plain)
            .map_err(|e| format!("ScsC compress failed: {e}"))?;
        encoder
            .finish()
            .map_err(|e| format!("ScsC compress failed: {e}"))?;
    }
    let mut iv = [0u8; 16];
    fill_random(&mut iv).map_err(|e| format!("random IV failed: {e}"))?;
    let pad = 16 - (compressed.len() % 16);
    let mut buffer = vec![0u8; compressed.len() + pad];
    buffer[..compressed.len()].copy_from_slice(&compressed);
    buffer[compressed.len()..].fill(pad as u8);
    let encrypted = cbc_encrypt(&mut buffer, &iv)?;
    let mut output = Vec::with_capacity(56 + encrypted.len());
    output.extend_from_slice(b"ScsC");
    output.extend_from_slice(&[0u8; 32]);
    output.extend_from_slice(&iv);
    let plain_len =
        u32::try_from(plain.len()).map_err(|_| "ScsC plaintext is too large".to_string())?;
    output.extend_from_slice(&plain_len.to_le_bytes());
    output.extend_from_slice(&encrypted);
    Ok(output)
}

fn cbc_decrypt(ciphertext: &[u8], iv: &[u8; 16]) -> Result<Vec<u8>, String> {
    if ciphertext.is_empty() || ciphertext.len() % 16 != 0 {
        return Err("ScsC ciphertext length is invalid".into());
    }
    let cipher = Aes256::new_from_slice(&SCSC_KEY).map_err(|_| "invalid ScsC key".to_string())?;
    let mut previous = *iv;
    let mut output = Vec::with_capacity(ciphertext.len());
    for chunk in ciphertext.chunks_exact(16) {
        let mut block = GenericArray::clone_from_slice(chunk);
        cipher.decrypt_block(&mut block);
        for index in 0..16 {
            block[index] ^= previous[index];
        }
        output.extend_from_slice(&block);
        previous.copy_from_slice(chunk);
    }
    Ok(output)
}

fn cbc_encrypt(plain: &mut [u8], iv: &[u8; 16]) -> Result<Vec<u8>, String> {
    if plain.is_empty() || plain.len() % 16 != 0 {
        return Err("ScsC plaintext length is invalid".into());
    }
    let cipher = Aes256::new_from_slice(&SCSC_KEY).map_err(|_| "invalid ScsC key".to_string())?;
    let mut previous = *iv;
    for chunk in plain.chunks_exact_mut(16) {
        for index in 0..16 {
            chunk[index] ^= previous[index];
        }
        let mut block = GenericArray::clone_from_slice(chunk);
        cipher.encrypt_block(&mut block);
        chunk.copy_from_slice(&block);
        previous.copy_from_slice(chunk);
    }
    Ok(plain.to_vec())
}

fn read_sii(path: &Path) -> Result<String, String> {
    let bytes = fs::read(path).map_err(|e| format!("read {} failed: {e}", path.display()))?;
    let plain = decode_scsc_or_plain(&bytes)?;
    Ok(String::from_utf8_lossy(&plain)
        .trim_start_matches('\u{feff}')
        .to_string())
}

fn escape_sii(value: &str) -> String {
    value.replace('\\', "\\\\").replace('"', "\\\"")
}

fn unescape_sii(value: &str) -> String {
    let mut bytes = Vec::with_capacity(value.len());
    let mut chars = value.chars();
    while let Some(ch) = chars.next() {
        if ch == '\\' {
            match chars.next() {
                Some('"') => bytes.push(b'"'),
                Some('\\') => bytes.push(b'\\'),
                Some('x') => {
                    let first = chars.next();
                    let second = chars.next();
                    match (first, second) {
                        (Some(first), Some(second))
                            if first.is_ascii_hexdigit() && second.is_ascii_hexdigit() =>
                        {
                            bytes.push(
                                u8::from_str_radix(&format!("{first}{second}"), 16)
                                    .unwrap_or_default(),
                            );
                        }
                        _ => {
                            bytes.extend_from_slice(b"\\x");
                            if let Some(first) = first {
                                let mut buffer = [0; 4];
                                bytes.extend_from_slice(first.encode_utf8(&mut buffer).as_bytes());
                            }
                            if let Some(second) = second {
                                let mut buffer = [0; 4];
                                bytes.extend_from_slice(second.encode_utf8(&mut buffer).as_bytes());
                            }
                        }
                    }
                }
                Some(other) => {
                    bytes.push(b'\\');
                    let mut buffer = [0; 4];
                    bytes.extend_from_slice(other.encode_utf8(&mut buffer).as_bytes());
                }
                None => bytes.push(b'\\'),
            }
        } else {
            let mut buffer = [0; 4];
            bytes.extend_from_slice(ch.encode_utf8(&mut buffer).as_bytes());
        }
    }
    String::from_utf8_lossy(&bytes).into_owned()
}

fn parse_field(text: &str, field: &str) -> Option<String> {
    text.lines().find_map(|line| {
        let trimmed = line.trim();
        let (raw_key, raw_value) = trimmed.split_once(':')?;
        if !raw_key.trim().eq_ignore_ascii_case(field) {
            return None;
        }
        let value = raw_value.trim();
        let value = value.strip_prefix('"')?.strip_suffix('"')?;
        Some(unescape_sii(value))
    })
}

fn parse_active_mods(text: &str) -> Vec<String> {
    text.lines()
        .filter_map(|line| {
            let trimmed = line.trim();
            if !trimmed.starts_with("active_mods[") {
                return None;
            }
            let value = trimmed.split_once(':')?.1.trim();
            Some(unescape_sii(value.strip_prefix('"')?.strip_suffix('"')?))
        })
        .collect()
}

fn decode_profile_folder_name(value: &str) -> Option<String> {
    if value.is_empty() || value.len() % 2 != 0 || !value.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    let bytes = (0..value.len())
        .step_by(2)
        .map(|index| u8::from_str_radix(&value[index..index + 2], 16).ok())
        .collect::<Option<Vec<_>>>()?;
    let decoded = String::from_utf8(bytes).ok()?;
    if decoded.chars().all(|c| !c.is_control()) {
        Some(decoded)
    } else {
        None
    }
}

fn list_profiles_from(root: Option<&Path>, location: &str) -> Vec<ProfileDto> {
    let Some(root) = root.filter(|p| p.is_dir()) else {
        return Vec::new();
    };
    let Ok(entries) = fs::read_dir(root) else {
        return Vec::new();
    };
    let mut result = Vec::new();
    for entry in entries.flatten() {
        let folder = entry.path();
        if !folder.is_dir() || !profile_sii(&folder).is_file() {
            continue;
        }
        let folder_id = folder
            .file_name()
            .and_then(|x| x.to_str())
            .unwrap_or_default()
            .to_string();
        let text = read_sii(&profile_sii(&folder)).unwrap_or_default();
        let active = parse_active_mods(&text);
        let fallback_name =
            decode_profile_folder_name(&folder_id).unwrap_or_else(|| folder_id.replace('_', " "));
        result.push(ProfileDto {
            id: format!("{location}:{folder_id}"),
            name: parse_field(&text, "profile_name").unwrap_or(fallback_name),
            company: parse_field(&text, "company_name").unwrap_or_default(),
            location: location.to_string(),
            folder: normalize_path(&folder),
            mod_count: active.len(),
            writable: location == "local",
        });
    }
    result.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    result
}

fn all_profiles(paths: &Paths) -> Vec<ProfileDto> {
    // The Mod Manager's primary profile selector is intentionally limited to
    // editable local profiles. Steam/Cloud profiles remain read-only data
    // sources for compatibility and are not shown as selectable profiles.
    list_profiles_from(Some(&paths.profiles_root), "local")
}

fn open_db(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create database directory failed: {e}"))?;
    }
    let connection = Connection::open(path).map_err(|e| format!("open database failed: {e}"))?;
    connection
        .execute_batch("PRAGMA busy_timeout=15000;")
        .map_err(|e| format!("configure database timeout failed: {e}"))?;
    connection
        .execute_batch(
            "PRAGMA journal_mode=WAL;
             CREATE TABLE IF NOT EXISTS mod_package_v2 (
               path TEXT PRIMARY KEY,
               mod_id TEXT NOT NULL,
               package_name TEXT NOT NULL,
               package_type TEXT NOT NULL,
               display_name TEXT NOT NULL,
               author TEXT NOT NULL DEFAULT '',
               version TEXT NOT NULL DEFAULT '',
               size INTEGER NOT NULL,
               modified_ms INTEGER NOT NULL,
               fingerprint INTEGER NOT NULL DEFAULT 0,
               scanned_at_ms INTEGER NOT NULL
             );
             CREATE INDEX IF NOT EXISTS ix_mod_display_v2 ON mod_package_v2(display_name COLLATE NOCASE);
             CREATE TABLE IF NOT EXISTS mod_metadata_state (
               path TEXT PRIMARY KEY,
               resolver_version INTEGER NOT NULL,
               updated_at_ms INTEGER NOT NULL
             );
             CREATE TABLE IF NOT EXISTS preset (
               profile_id TEXT NOT NULL,
               name TEXT NOT NULL,
               active_mods_json TEXT NOT NULL,
               PRIMARY KEY(profile_id, name)
             );
             CREATE TABLE IF NOT EXISTS localization_package_v2 (
               package_path TEXT NOT NULL,
               target_locale TEXT NOT NULL,
               package_type TEXT NOT NULL,
               file_size INTEGER NOT NULL,
               modified_ms INTEGER NOT NULL,
               scanned_at_ms INTEGER NOT NULL,
               PRIMARY KEY(package_path, target_locale)
             );
             CREATE TABLE IF NOT EXISTS localization_entry_v2 (
               package_path TEXT NOT NULL,
               target_locale TEXT NOT NULL,
               entry_order INTEGER NOT NULL,
               key TEXT NOT NULL,
               value TEXT NOT NULL,
               source_name TEXT NOT NULL DEFAULT '',
               source_path TEXT NOT NULL,
               package_name TEXT NOT NULL,
               category TEXT NOT NULL,
               status TEXT NOT NULL,
               locale_key_present INTEGER NOT NULL,
               def_locale_key_present INTEGER NOT NULL,
               unit_name TEXT NOT NULL,
               locale_key TEXT NOT NULL,
               PRIMARY KEY(package_path, target_locale, entry_order)
             );
             CREATE INDEX IF NOT EXISTS ix_localization_entry_v2_key
               ON localization_entry_v2(target_locale, key);
             CREATE TABLE IF NOT EXISTS mod_index_state (
               id INTEGER PRIMARY KEY CHECK (id = 1),
               completed_at_ms INTEGER NOT NULL,
               package_count INTEGER NOT NULL
             );
             CREATE TABLE IF NOT EXISTS mod_package_header_state (
               path TEXT PRIMARY KEY,
               size INTEGER NOT NULL,
               modified_ms INTEGER NOT NULL,
               is_directory INTEGER NOT NULL
             );
             CREATE TABLE IF NOT EXISTS app_setting (
               key TEXT PRIMARY KEY,
               value TEXT NOT NULL
             );",
        )
        .map_err(|e| format!("initialize database failed: {e}"))?;
    ensure_mod_fingerprint_column(&connection)?;
    ensure_header_state_table(&connection)?;
    ensure_mod_index_state_table(&connection)?;
    ensure_media_cache_table(&connection)?;
    ensure_localization_source_name_column(&connection)?;
    Ok(connection)
}

fn acquire_db_write_lock() -> std::sync::MutexGuard<'static, ()> {
    DB_WRITE_LOCK
        .get_or_init(|| Mutex::new(()))
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

#[derive(Clone, Debug)]
struct ShallowCandidate {
    path: PathBuf,
    size: u64,
    modified_ms: i64,
    is_directory: bool,
    workshop: bool,
}

fn shallow_package_candidates(
    root: Option<&Path>,
    workshop: bool,
    cancelled: &AtomicBool,
) -> Vec<ShallowCandidate> {
    let Some(root) = root else { return Vec::new() };
    let Ok(entries) = fs::read_dir(root) else {
        return Vec::new();
    };
    entries
        .flatten()
        .filter_map(|entry| {
            if cancelled.load(Ordering::Relaxed) {
                return None;
            }
            let path = entry.path();
            let metadata = fs::metadata(&path).ok()?;
            let is_directory = metadata.is_dir();
            let extension = path
                .extension()
                .and_then(|x| x.to_str())
                .unwrap_or_default()
                .to_ascii_lowercase();
            if !is_directory && extension != "scs" && extension != "zip" {
                return None;
            }
            // Directory candidates use the same bounded info-file signature
            // that is persisted in the header table. Reading only manifest /
            // description files keeps startup incremental without treating
            // unrelated game assets as a change signal.
            let (size, modified_ms) = if is_directory {
                let (info_size, info_modified, _) = directory_info_signature(&path, cancelled);
                (info_size, info_modified)
            } else {
                let modified_ms = metadata
                    .modified()
                    .ok()
                    .and_then(|v| v.duration_since(UNIX_EPOCH).ok())
                    .map(|v| v.as_millis() as i64)
                    .unwrap_or_default();
                (metadata.len(), modified_ms)
            };
            Some(ShallowCandidate {
                path,
                size,
                modified_ms,
                is_directory,
                workshop,
            })
        })
        .collect()
}

fn discover_single_candidate(
    candidate: &ShallowCandidate,
    cancelled: &AtomicBool,
    progress: &mut impl FnMut(&str, usize, usize, &str, &Path),
) -> Option<ModDto> {
    if cancelled.load(Ordering::Relaxed) {
        return None;
    }
    let path = &candidate.path;
    let extension = path
        .extension()
        .and_then(|x| x.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    let id = if candidate.workshop {
        path.file_name()
            .and_then(|x| x.to_str())
            .unwrap_or_default()
            .to_string()
    } else {
        path.file_stem()
            .and_then(|x| x.to_str())
            .unwrap_or_default()
            .to_string()
    };
    let display_name = if candidate.workshop {
        workshop_cached_title(&id).unwrap_or_else(|| id.replace('_', " "))
    } else {
        id.replace('_', " ")
    };
    progress(
        if candidate.workshop {
            "workshop"
        } else {
            "local"
        },
        0,
        1,
        &display_name,
        path,
    );
    let (size, modified_ms, fingerprint) = if candidate.is_directory {
        directory_info_signature(path, cancelled)
    } else {
        let fingerprint = candidate
            .size
            .wrapping_mul(1099511628211)
            .wrapping_add(candidate.modified_ms as u64);
        (candidate.size, candidate.modified_ms, fingerprint)
    };
    Some(ModDto {
        id: id.clone(),
        package_name: id,
        path: normalize_path(path),
        package_type: if candidate.workshop {
            "workshop".into()
        } else if candidate.is_directory {
            "directory".into()
        } else {
            extension
        },
        display_name,
        author: String::new(),
        version: String::new(),
        size,
        modified_ms,
        enabled: false,
        category: "unknown".into(),
        fingerprint,
    })
}

fn ensure_media_cache_table(connection: &Connection) -> Result<(), String> {
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS mod_media_cache (
            path TEXT PRIMARY KEY, size INTEGER NOT NULL, modified_ms INTEGER NOT NULL,
            fingerprint INTEGER NOT NULL, icon_url TEXT, preview_url TEXT,
            cached_at_ms INTEGER NOT NULL
        );",
        )
        .map_err(|e| format!("initialize media cache failed: {e}"))?;
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS mod_media_cache_meta (
                path TEXT PRIMARY KEY,
                resolver_version INTEGER NOT NULL
            );",
        )
        .map_err(|e| format!("initialize media cache metadata failed: {e}"))
}

fn ensure_header_state_table(connection: &Connection) -> Result<(), String> {
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS mod_package_header_state (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                modified_ms INTEGER NOT NULL,
                is_directory INTEGER NOT NULL
            );",
        )
        .map_err(|e| format!("initialize mod header state failed: {e}"))
}

fn ensure_mod_index_state_table(connection: &Connection) -> Result<(), String> {
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS mod_index_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                completed_at_ms INTEGER NOT NULL,
                package_count INTEGER NOT NULL
            );",
        )
        .map_err(|e| format!("initialize mod index state failed: {e}"))
}

fn backfill_mod_header_state(connection: &Connection) -> Result<(), String> {
    // Older indexes predate the header table (or only populated it for a
    // subset of packages). Reconstruct it from persisted rows so startup does
    // not re-open every unchanged package just to establish cache state.
    let _write_lock = acquire_db_write_lock();
    connection
        .execute(
            "INSERT OR IGNORE INTO mod_package_header_state(path,size,modified_ms,is_directory)
             SELECT path,size,modified_ms,CASE WHEN package_type='directory' THEN 1 ELSE 0 END
             FROM mod_package_v2",
            [],
        )
        .map_err(|e| format!("backfill mod header state failed: {e}"))?;
    // Repair rows written by older versions. Workshop packages are physical
    // directories even though their logical package type is "workshop"; use
    // the filesystem as the source of truth so they can hit the cache on the
    // next startup instead of being re-read forever.
    connection
        .execute(
            "UPDATE mod_package_header_state
             SET size = COALESCE(
                     (SELECT m.size FROM mod_package_v2 m
                      WHERE m.path = mod_package_header_state.path), size
                 ),
                 modified_ms = COALESCE(
                     (SELECT m.modified_ms FROM mod_package_v2 m
                      WHERE m.path = mod_package_header_state.path), modified_ms
                 ),
                 is_directory = CASE
                     WHEN EXISTS (
                         SELECT 1 FROM mod_package_v2 m
                         WHERE m.path = mod_package_header_state.path
                           AND (m.package_type IN ('directory','workshop')
                                OR m.path LIKE '%\\workshop\\%')
                     ) THEN 1
                     ELSE is_directory
                 END
             WHERE path IN (SELECT path FROM mod_package_v2)",
            [],
        )
        .map_err(|e| format!("repair mod header state failed: {e}"))?;
    Ok(())
}

fn ensure_mod_fingerprint_column(connection: &Connection) -> Result<(), String> {
    let has_column = connection
        .prepare("PRAGMA table_info(mod_package_v2)")
        .map_err(|e| format!("inspect mod index schema failed: {e}"))?
        .query_map([], |row| row.get::<_, String>(1))
        .map_err(|e| format!("inspect mod index schema failed: {e}"))?
        .filter_map(Result::ok)
        .any(|name| name == "fingerprint");
    if !has_column {
        connection
            .execute(
                "ALTER TABLE mod_package_v2 ADD COLUMN fingerprint INTEGER NOT NULL DEFAULT 0",
                [],
            )
            .map_err(|e| format!("upgrade mod index schema failed: {e}"))?;
    }
    Ok(())
}

fn ensure_localization_source_name_column(connection: &Connection) -> Result<(), String> {
    let has_column = connection
        .prepare("PRAGMA table_info(localization_entry_v2)")
        .map_err(|e| format!("inspect localization schema failed: {e}"))?
        .query_map([], |row| row.get::<_, String>(1))
        .map_err(|e| format!("inspect localization schema failed: {e}"))?
        .filter_map(Result::ok)
        .any(|name| name == "source_name");
    if !has_column {
        connection
            .execute(
                "ALTER TABLE localization_entry_v2 ADD COLUMN source_name TEXT NOT NULL DEFAULT ''",
                [],
            )
            .map_err(|e| format!("upgrade localization schema failed: {e}"))?;
    }
    Ok(())
}

fn manifest_for(path: &Path) -> (String, String, String, String, String) {
    let mut manifest = if path.is_dir() {
        read_directory_info_manifest(path)
    } else {
        archive_core::read_manifest(path).unwrap_or_default()
    };
    if manifest.package_name.is_empty()
        && manifest.display_name.is_empty()
        && manifest.author.is_empty()
        && manifest.version.is_empty()
        && path.is_file()
    {
        if is_zip_archive(path) {
            if let Some(text) = read_zip_entry_text(path, "manifest.sii") {
                manifest = archive_core::parse_manifest(&text);
            }
        }
        if manifest.package_name.is_empty()
            && manifest.display_name.is_empty()
            && manifest.author.is_empty()
            && manifest.version.is_empty()
        {
            if let Some(text) = external_archive_manifest(path) {
                manifest = archive_core::parse_manifest(&text);
            }
        }
    }
    (
        manifest.package_name,
        manifest.display_name,
        manifest.author,
        manifest.version,
        manifest.icon_filename,
    )
}

fn read_directory_info_manifest(root: &Path) -> archive_core::Manifest {
    const INFO_FILES: [&str; 2] = ["manifest.sii", "mods_info.sii"];
    let mut fallback = archive_core::Manifest::default();
    let Ok(entries) = fs::read_dir(root) else {
        return fallback;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let Some(name) = path.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        if !INFO_FILES
            .iter()
            .any(|candidate| name.eq_ignore_ascii_case(candidate))
        {
            continue;
        }
        let Ok(text) = fs::read_to_string(&path) else {
            continue;
        };
        let parsed = archive_core::parse_manifest(&text);
        if name.eq_ignore_ascii_case("manifest.sii") {
            return parsed;
        }
        if fallback.display_name.is_empty()
            && fallback.package_name.is_empty()
            && fallback.author.is_empty()
            && fallback.version.is_empty()
        {
            fallback = parsed;
        }
    }
    fallback
}

const MAX_MEDIA_BYTES: u64 = 8 * 1024 * 1024;
const METADATA_RESOLVER_VERSION: i64 = 1;
// Bump when archive routing or manifest/icon extraction changes. This
// invalidates old negative media cache rows once, then restores persistence.
const MEDIA_RESOLVER_VERSION: i64 = 5;

fn media_extension(path: &str) -> Option<&'static str> {
    let extension = Path::new(path)
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    if extension.eq_ignore_ascii_case("jpg") || extension.eq_ignore_ascii_case("jpeg") {
        Some("image/jpeg")
    } else if extension.eq_ignore_ascii_case("png") {
        Some("image/png")
    } else if extension.eq_ignore_ascii_case("webp") {
        Some("image/webp")
    } else if extension.eq_ignore_ascii_case("gif") {
        Some("image/gif")
    } else if extension.eq_ignore_ascii_case("bmp") {
        Some("image/bmp")
    } else {
        None
    }
}

fn data_url(bytes: Vec<u8>, name: &str) -> Option<String> {
    if bytes.is_empty() || bytes.len() as u64 > MAX_MEDIA_BYTES {
        return None;
    }
    let mime = media_extension(name)?;
    Some(format!("data:{mime};base64,{}", BASE64.encode(bytes)))
}

fn media_cache_stem(row: &ModDto) -> String {
    let mut hasher = Sha1::new();
    hasher.update(row.path.as_bytes());
    hasher.update([0]);
    hasher.update(row.size.to_le_bytes());
    hasher.update(row.modified_ms.to_le_bytes());
    hasher.update(row.fingerprint.to_le_bytes());
    format!("{:x}", hasher.finalize())
}

fn media_cache_directory() -> Option<PathBuf> {
    let directory = cache_directory()?.join("mod_previews");
    fs::create_dir_all(&directory).ok()?;
    Some(directory)
}

fn data_url_bytes(value: &str) -> Option<(Vec<u8>, &'static str)> {
    let (header, payload) = value.strip_prefix("data:")?.split_once(',')?;
    if !header.to_ascii_lowercase().contains(";base64") {
        return None;
    }
    let mime = header.split(';').next()?.trim().to_ascii_lowercase();
    let extension = match mime.as_str() {
        "image/jpeg" => "jpg",
        "image/png" => "png",
        "image/webp" => "webp",
        "image/gif" => "gif",
        "image/bmp" => "bmp",
        _ => return None,
    };
    let bytes = BASE64.decode(payload).ok()?;
    if bytes.is_empty() || bytes.len() as u64 > MAX_MEDIA_BYTES {
        return None;
    }
    Some((bytes, extension))
}

fn persist_resolved_media(row: &ModDto, media: &ModMediaDto) {
    let Some(value) = media.preview_url.as_deref().or(media.icon_url.as_deref()) else {
        return;
    };
    let Some((bytes, extension)) = data_url_bytes(value) else {
        return;
    };
    let Some(directory) = media_cache_directory() else {
        return;
    };
    let target = directory.join(format!("{}.{}", media_cache_stem(row), extension));
    if target.is_file() {
        return;
    }
    let temporary = target.with_extension(format!("{extension}.tmp-{}", std::process::id()));
    if fs::write(&temporary, bytes).is_ok() {
        let _ = fs::rename(&temporary, &target);
        let _ = fs::remove_file(&temporary);
    }
}

fn persisted_media_url(row: &ModDto) -> Option<String> {
    let directory = media_cache_directory()?;
    let stem = media_cache_stem(row);
    for extension in ["jpg", "jpeg", "png", "webp", "gif", "bmp"] {
        let candidate = directory.join(format!("{stem}.{extension}"));
        if candidate.is_file()
            && fs::metadata(&candidate)
                .map(|metadata| metadata.len() <= MAX_MEDIA_BYTES)
                .unwrap_or(false)
        {
            if let Ok(bytes) = fs::read(&candidate) {
                if let Some(url) = data_url(bytes, candidate.to_string_lossy().as_ref()) {
                    return Some(url);
                }
            }
        }
    }
    None
}

fn hide_child_process(command: &mut std::process::Command) {
    #[cfg(windows)]
    {
        command.creation_flags(0x08000000);
    }
}

fn run_external_command_cancellable(tool: &Path, args: &[String], cancelled: &AtomicBool) -> bool {
    let mut command = std::process::Command::new(tool);
    hide_child_process(&mut command);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    let Ok(mut child) = command.args(args).spawn() else {
        return false;
    };
    loop {
        if cancelled.load(Ordering::Relaxed) {
            let _ = child.kill();
            let _ = child.wait();
            return false;
        }
        match child.try_wait() {
            Ok(Some(status)) => return status.success(),
            Ok(None) => std::thread::sleep(std::time::Duration::from_millis(40)),
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return false;
            }
        }
    }
}

fn read_zip_entry_bytes(path: &Path, wanted: &str) -> Option<Vec<u8>> {
    // Never probe non-ZIP SCS/HashFS packages with `ZipArchive::new`.
    // HashFS packages can be several gigabytes and ZIP's central-directory
    // search would otherwise scan the tail of the whole file during startup.
    if !is_zip_archive(path) {
        return None;
    }
    let file = fs::File::open(path).ok()?;
    let mut archive = ZipArchive::new(file).ok()?;
    let wanted = wanted
        .replace('\\', "/")
        .trim_start_matches('/')
        .to_string();
    let wanted_lower = wanted.to_ascii_lowercase();
    let mut selected = None;
    for index in 0..archive.len() {
        let entry = archive.by_index(index).ok()?;
        let name = entry.name().replace('\\', "/");
        let lower = name.to_ascii_lowercase();
        if lower == wanted_lower
            || lower
                .rsplit('/')
                .next()
                .is_some_and(|value| value == wanted_lower)
        {
            selected = Some(index);
            break;
        }
    }
    let index = selected?;
    let mut entry = archive.by_index(index).ok()?;
    if entry.size() > MAX_MEDIA_BYTES {
        return None;
    }
    let mut bytes = Vec::with_capacity(entry.size() as usize);
    entry.read_to_end(&mut bytes).ok()?;
    Some(bytes)
}

fn read_zip_entry_text(path: &Path, wanted: &str) -> Option<String> {
    let bytes = read_zip_entry_bytes(path, wanted)?;
    String::from_utf8(bytes).ok()
}

fn manifest_icon_path(value: &str) -> Option<String> {
    let normalized = value.trim().replace('\\', "/");
    let normalized = normalized.trim_start_matches('/');
    if normalized.is_empty() {
        return None;
    }
    let path = Path::new(normalized);
    if path.is_absolute()
        || path
            .components()
            .any(|component| matches!(component, std::path::Component::ParentDir))
    {
        return None;
    }
    Some(normalized.to_string())
}

fn directory_image_path(root: &Path, icon_filename: &str) -> Option<PathBuf> {
    let icon = manifest_icon_path(icon_filename)?;
    let candidate = root.join(icon);
    if candidate.is_file()
        && media_extension(candidate.to_string_lossy().as_ref()).is_some()
        && fs::metadata(&candidate)
            .map(|metadata| metadata.len() <= MAX_MEDIA_BYTES)
            .unwrap_or(false)
    {
        Some(candidate)
    } else {
        None
    }
}

fn package_media_url(path: &Path, icon_filename: &str) -> Option<String> {
    if path.is_dir() {
        if let Some(image) = directory_image_path(path, icon_filename) {
            if let Ok(bytes) = fs::read(&image) {
                if let Some(url) = data_url(bytes, image.to_string_lossy().as_ref()) {
                    return Some(url);
                }
            }
        }
    } else if path.is_file() {
        if let Some(candidate) = manifest_icon_path(icon_filename) {
            if let Some(bytes) = read_zip_entry_bytes(path, &candidate) {
                if let Some(url) = data_url(bytes, &candidate) {
                    return Some(url);
                }
            }
        }
    }
    None
}

fn cached_workshop_preview_url(mod_id: &str) -> Option<String> {
    let cache = cache_directory()?.join("workshop_previews");
    for extension in ["jpg", "jpeg", "png", "webp", "gif", "bmp"] {
        let candidate = cache.join(format!("{mod_id}.{extension}"));
        if candidate.is_file()
            && fs::metadata(&candidate)
                .map(|metadata| metadata.len() <= MAX_MEDIA_BYTES)
                .unwrap_or(false)
        {
            if let Ok(bytes) = fs::read(&candidate) {
                if let Some(url) = data_url(bytes, candidate.to_string_lossy().as_ref()) {
                    return Some(url);
                }
            }
        }
    }
    None
}

fn workshop_preview_failure_marker(mod_id: &str) -> Option<PathBuf> {
    Some(
        cache_directory()?
            .join("workshop_previews")
            .join(format!("{mod_id}.failed")),
    )
}

fn workshop_preview_download_suppressed(mod_id: &str) -> bool {
    let Some(marker) = workshop_preview_failure_marker(mod_id) else {
        return false;
    };
    let Ok(metadata) = fs::metadata(marker) else {
        return false;
    };
    metadata
        .modified()
        .ok()
        .and_then(|value| value.elapsed().ok())
        .map(|elapsed| elapsed.as_secs() < 24 * 60 * 60)
        .unwrap_or(false)
}

fn mark_workshop_preview_failure(mod_id: &str) {
    let Some(marker) = workshop_preview_failure_marker(mod_id) else {
        return;
    };
    if let Some(parent) = marker.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let _ = fs::write(marker, now_ms().to_string());
}

fn clear_workshop_preview_failure(mod_id: &str) {
    if let Some(marker) = workshop_preview_failure_marker(mod_id) {
        let _ = fs::remove_file(marker);
    }
}

fn download_workshop_preview(mod_id: &str) -> Option<String> {
    if mod_id.is_empty()
        || !mod_id.chars().all(|value| value.is_ascii_digit())
        || workshop_preview_download_suppressed(mod_id)
    {
        return None;
    }
    if let Some(cached) = cached_workshop_preview_url(mod_id) {
        return Some(cached);
    }
    let directory = media_cache_directory()?;
    let client = reqwest::blocking::Client::builder()
        .user_agent("ETS2ModManager/1.0")
        .timeout(std::time::Duration::from_secs(20))
        .build()
        .ok()?;
    let response = client
        .post("https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/")
        .form(&[("itemcount", "1"), ("publishedfileids[0]", mod_id)])
        .send()
        .ok()?;
    if !response.status().is_success() {
        mark_workshop_preview_failure(mod_id);
        return None;
    }
    let payload: serde_json::Value = serde_json::from_str(&response.text().ok()?).ok()?;
    let api_preview_url = payload
        .get("response")
        .and_then(|value| value.get("publishedfiledetails"))
        .and_then(|value| value.as_array())
        .and_then(|items| items.first())
        .and_then(|item| item.get("preview_url"))
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string);
    let preview_url = if let Some(value) = api_preview_url {
        value
    } else {
        // Some Workshop items return result=9 from the API while the public
        // Workshop page still exposes its og:image preview. Keep this as a
        // fallback for legacy/package IDs that Steam no longer indexes.
        let page = client
            .get(format!(
                "https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}"
            ))
            .send()
            .ok()
            .and_then(|value| value.text().ok());
        let Some(page) = page else {
            mark_workshop_preview_failure(mod_id);
            return None;
        };
        let pattern =
            Regex::new(r#"(?is)<meta[^>]+property=["']og:image["'][^>]+content=["']([^"']+)["']"#)
                .ok();
        let Some(value) = pattern
            .and_then(|regex| regex.captures(&page))
            .and_then(|captures| captures.get(1))
            .map(|value| value.as_str().trim().to_string())
            .filter(|value| !value.is_empty())
        else {
            mark_workshop_preview_failure(mod_id);
            return None;
        };
        value
    };

    let image = client.get(&preview_url).send().ok()?;
    if !image.status().is_success() {
        mark_workshop_preview_failure(mod_id);
        return None;
    }
    let content_type = image
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default()
        .split(';')
        .next()
        .unwrap_or_default()
        .to_ascii_lowercase();
    let extension = match content_type.as_str() {
        "image/jpeg" => "jpg".to_string(),
        "image/png" => "png".to_string(),
        "image/webp" => "webp".to_string(),
        "image/gif" => "gif".to_string(),
        "image/bmp" => "bmp".to_string(),
        _ => Path::new(&preview_url)
            .extension()
            .and_then(|value| value.to_str())
            .map(|value| value.to_ascii_lowercase())
            .filter(|value| ["jpg", "jpeg", "png", "webp", "gif", "bmp"].contains(&value.as_str()))
            .unwrap_or_else(|| "jpg".to_string()),
    };
    let bytes = image.bytes().ok()?;
    if bytes.is_empty() || bytes.len() as u64 > MAX_MEDIA_BYTES {
        mark_workshop_preview_failure(mod_id);
        return None;
    }
    let target = directory.join(format!("{mod_id}.{extension}"));
    let temporary = directory.join(format!("{mod_id}.{extension}.tmp-{}", std::process::id()));
    if fs::write(&temporary, &bytes).is_err() {
        mark_workshop_preview_failure(mod_id);
        return None;
    }
    if fs::rename(&temporary, &target).is_err() {
        let _ = fs::remove_file(&temporary);
        mark_workshop_preview_failure(mod_id);
        return None;
    }
    clear_workshop_preview_failure(mod_id);
    cached_workshop_preview_url(mod_id)
}

fn extractor_path() -> Option<PathBuf> {
    let mut roots = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        roots.extend(exe.ancestors().map(Path::to_path_buf));
    }
    if let Ok(current) = std::env::current_dir() {
        roots.extend(current.ancestors().map(Path::to_path_buf));
    }
    let mut candidates = Vec::new();
    for root in roots {
        candidates.push(root.join("assets/tools/extractor-2025-10-21.exe"));
        candidates.push(root.join("assets/tools/extractor.exe"));
    }
    candidates.into_iter().find(|path| path.is_file())
}

fn sxc_path() -> Option<PathBuf> {
    let mut roots = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        roots.extend(exe.ancestors().map(Path::to_path_buf));
    }
    if let Ok(current) = std::env::current_dir() {
        roots.extend(current.ancestors().map(Path::to_path_buf));
    }
    roots
        .into_iter()
        .map(|root| root.join("assets/tools/sxc64.exe"))
        .find(|path| path.is_file())
}

fn archive_kind(path: &Path) -> archive_core::ArchiveKind {
    let Ok(mut file) = fs::File::open(path) else {
        return archive_core::ArchiveKind::Unknown;
    };
    let mut header = [0u8; 4];
    if file.read_exact(&mut header).is_err() {
        return archive_core::ArchiveKind::Unknown;
    }
    archive_core::detect_kind(&header)
}

fn is_zip_archive(path: &Path) -> bool {
    let Ok(mut file) = fs::File::open(path) else {
        return false;
    };
    let mut header = [0u8; 4];
    if file.read_exact(&mut header).is_err() {
        return false;
    }
    matches!(&header, b"PK\x03\x04" | b"PK\x05\x06" | b"PK\x07\x08")
}

fn extractor_temp_directory(path: &Path, suffix: &str) -> Option<PathBuf> {
    let mut hasher = Sha1::new();
    hasher.update(normalize_path(path).as_bytes());
    hasher.update(suffix.as_bytes());
    let key = format!("{:x}", hasher.finalize());
    let temp = std::env::temp_dir().join(format!("ets2mm-preview-{}-{key}", std::process::id()));
    fs::create_dir_all(&temp).ok()?;
    Some(temp)
}

fn external_tool_path(path: &Path) -> PathBuf {
    let value = path.to_string_lossy();
    if let Some(rest) = value.strip_prefix(r"\\?\UNC\") {
        PathBuf::from(format!(r"\\{rest}"))
    } else if let Some(rest) = value.strip_prefix(r"\\?\") {
        PathBuf::from(rest)
    } else {
        path.to_path_buf()
    }
}

// HashFS archives without a top-level directory listing require --deep so
// the extractor scans contained files for paths. Archives that do have a
// listing (def.scs, dlc_*.scs) must not use --deep: it forces a full hash
// scan of every file, which is extremely slow on multi-gigabyte archives.
fn hashfs_has_top_level_directory(path: &Path) -> bool {
    let Some(extractor) = extractor_path() else {
        return false;
    };
    let mut command = std::process::Command::new(&extractor);
    hide_child_process(&mut command);
    let Ok(output) = command.arg(external_tool_path(path)).arg("--list").output() else {
        return false;
    };
    // With a listing the extractor writes the contained paths to stdout;
    // without one it only reports "Top level directory is missing" on stderr.
    let stdout = String::from_utf8_lossy(&output.stdout);
    !stdout.trim().is_empty()
}

fn external_archive_manifest(path: &Path) -> Option<String> {
    if !path.is_file() {
        return None;
    }
    let temp = extractor_temp_directory(path, "manifest")?;
    let kind = archive_kind(path);
    let (tool, args): (PathBuf, Vec<String>) = match kind {
        archive_core::ArchiveKind::HashFs => {
            let extractor = extractor_path()?;
            (
                extractor,
                vec![
                    external_tool_path(path).to_string_lossy().into_owned(),
                    "--deep".into(),
                    "--partial=/manifest.sii".into(),
                    "-d".into(),
                    temp.to_string_lossy().into_owned(),
                    "-s".into(),
                ],
            )
        }
        archive_core::ArchiveKind::Aem | archive_core::ArchiveKind::Zip => {
            let sxc = sxc_path()?;
            (
                sxc,
                vec![
                    external_tool_path(path).to_string_lossy().into_owned(),
                    "-o".into(),
                    temp.to_string_lossy().into_owned(),
                    "-f".into(),
                    "/manifest.sii".into(),
                    "-q".into(),
                ],
            )
        }
        archive_core::ArchiveKind::Unknown => return None,
    };
    let mut command = std::process::Command::new(&tool);
    hide_child_process(&mut command);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    let status = command.args(args).status().ok();
    let manifest = if status.is_some_and(|value| value.success()) {
        find_extracted_entry(&temp, "manifest.sii").and_then(|path| fs::read_to_string(path).ok())
    } else {
        None
    };
    let _ = fs::remove_dir_all(&temp);
    manifest
}

fn external_archive_image(path: &Path, icon_filename: &str) -> Option<String> {
    let icon = manifest_icon_path(icon_filename)?;
    if !path.is_file() {
        return None;
    }
    let temp = extractor_temp_directory(path, "image")?;
    let partial = format!("/{icon}");
    let kind = archive_kind(path);
    let (tool, args): (PathBuf, Vec<String>) = match kind {
        archive_core::ArchiveKind::HashFs => {
            let extractor = extractor_path()?;
            (
                extractor,
                vec![
                    external_tool_path(path).to_string_lossy().into_owned(),
                    "--deep".into(),
                    format!("--partial={partial}"),
                    "-d".into(),
                    temp.to_string_lossy().into_owned(),
                    "-s".into(),
                ],
            )
        }
        archive_core::ArchiveKind::Aem | archive_core::ArchiveKind::Zip => {
            let sxc = sxc_path()?;
            (
                sxc,
                vec![
                    external_tool_path(path).to_string_lossy().into_owned(),
                    "-o".into(),
                    temp.to_string_lossy().into_owned(),
                    "-f".into(),
                    partial,
                    "-q".into(),
                ],
            )
        }
        archive_core::ArchiveKind::Unknown => return None,
    };
    let mut command = std::process::Command::new(&tool);
    hide_child_process(&mut command);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    let status = command.args(args).status().ok();
    let mut result = None;
    if status.is_some_and(|value| value.success()) {
        let mut stack = vec![temp.clone()];
        while let Some(current) = stack.pop() {
            let Ok(entries) = fs::read_dir(&current) else {
                continue;
            };
            for entry in entries.flatten() {
                let candidate = entry.path();
                if candidate.is_dir() {
                    stack.push(candidate);
                    continue;
                }
                if media_extension(candidate.to_string_lossy().as_ref()).is_none() {
                    continue;
                }
                if fs::metadata(&candidate)
                    .map(|metadata| metadata.len() <= MAX_MEDIA_BYTES)
                    .unwrap_or(false)
                {
                    if let Ok(bytes) = fs::read(&candidate) {
                        result = data_url(bytes, candidate.to_string_lossy().as_ref());
                        if result.is_some() {
                            break;
                        }
                    }
                }
            }
            if result.is_some() {
                break;
            }
        }
    }
    let _ = fs::remove_dir_all(&temp);
    result
}

fn find_extracted_entry(root: &Path, wanted: &str) -> Option<PathBuf> {
    let wanted = wanted
        .replace('\\', "/")
        .trim_start_matches('/')
        .to_ascii_lowercase();
    let mut stack = vec![root.to_path_buf()];
    while let Some(current) = stack.pop() {
        for entry in fs::read_dir(&current).ok()?.flatten() {
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
                continue;
            }
            let relative = path
                .strip_prefix(root)
                .ok()?
                .to_string_lossy()
                .replace('\\', "/");
            if relative.to_ascii_lowercase() == wanted
                || relative
                    .rsplit('/')
                    .next()
                    .is_some_and(|name| name == wanted)
            {
                return Some(path);
            }
        }
    }
    None
}

fn resolve_mod_media(row: &ModDto) -> ModMediaDto {
    let workshop_id = workshop_id_from_value(&row.package_name)
        .or_else(|| workshop_id_from_value(&row.id))
        .unwrap_or_else(|| row.id.trim().trim_end_matches("_workshop").to_string());
    let cached_preview = if is_workshop(row) {
        cached_workshop_preview_url(&workshop_id)
            .or_else(|| workshop_cached_preview_url(&workshop_id))
            .or_else(|| download_workshop_preview(&workshop_id))
    } else {
        None
    };
    let media = if let Some(preview) = cached_preview {
        // Workshop metadata already contains a stable preview URL (or a local
        // downloaded preview). Do not open the Workshop package or invoke the
        // extractor again when that cache is available.
        ModMediaDto {
            mod_id: row.id.clone(),
            icon_url: Some(preview.clone()),
            preview_url: Some(preview),
        }
    } else if let Some(persisted) = persisted_media_url(row) {
        // A persisted extraction is authoritative for this package fingerprint.
        ModMediaDto {
            mod_id: row.id.clone(),
            icon_url: Some(persisted.clone()),
            preview_url: Some(persisted),
        }
    } else {
        let package_url = {
            let path = Path::new(&row.path);
            // Read only the ZIP manifest entry, not the entire archive.
            let manifest = if path.is_file() {
                let inline = if is_zip_archive(path) {
                    read_zip_entry_text(path, "manifest.sii")
                } else {
                    None
                };
                inline.or_else(|| external_archive_manifest(path))
            } else {
                fs::read_to_string(path.join("manifest.sii")).ok()
            };
            let icon_filename = manifest
                .map(|text| archive_core::parse_manifest(&text).icon_filename)
                .unwrap_or_default();
            package_media_url(path, &icon_filename)
                .or_else(|| external_archive_image(path, &icon_filename))
        };
        ModMediaDto {
            mod_id: row.id.clone(),
            icon_url: package_url.clone(),
            preview_url: package_url,
        }
    };
    persist_resolved_media(row, &media);
    media
}

fn cached_mod_media<F>(
    connection: &Connection,
    row: &ModDto,
    resolve: F,
) -> Result<ModMediaDto, String>
where
    F: FnOnce(&ModDto) -> ModMediaDto,
{
    let cached = connection
        .query_row(
            "SELECT c.icon_url, c.preview_url FROM mod_media_cache c
         LEFT JOIN mod_media_cache_meta m ON m.path = c.path
         WHERE c.path=?1 AND c.size=?2 AND c.modified_ms=?3 AND c.fingerprint=?4
         AND COALESCE(m.resolver_version, 1)=?5
         AND (c.icon_url IS NOT NULL OR c.preview_url IS NOT NULL OR c.cached_at_ms>0)",
            params![
                row.path,
                row.size as i64,
                row.modified_ms,
                row.fingerprint as i64,
                MEDIA_RESOLVER_VERSION
            ],
            |value| {
                Ok(ModMediaDto {
                    mod_id: row.id.clone(),
                    icon_url: value.get(0)?,
                    preview_url: value.get(1)?,
                })
            },
        )
        .optional()
        .map_err(|e| format!("read media cache failed: {e}"))?;
    if let Some(cached) = cached {
        // Backfill the file cache for entries written by older versions that
        // only persisted a data URL in SQLite.
        persist_resolved_media(row, &cached);
        return Ok(cached);
    }
    let media = resolve(row);
    // Cache misses too, so packages without artwork are not repeatedly opened.
    let _write_lock = acquire_db_write_lock();
    connection.execute(
        "INSERT INTO mod_media_cache(path,size,modified_ms,fingerprint,icon_url,preview_url,cached_at_ms)
         VALUES(?1,?2,?3,?4,?5,?6,?7)
         ON CONFLICT(path) DO UPDATE SET size=excluded.size,modified_ms=excluded.modified_ms,
         fingerprint=excluded.fingerprint,icon_url=excluded.icon_url,preview_url=excluded.preview_url,
         cached_at_ms=excluded.cached_at_ms",
        params![
            row.path,
            row.size as i64,
            row.modified_ms,
            row.fingerprint as i64,
            media.icon_url,
            media.preview_url,
            now_ms()
        ],
    ).map_err(|e| format!("persist media cache failed: {e}"))?;
    connection
        .execute(
            "INSERT INTO mod_media_cache_meta(path,resolver_version) VALUES(?1,?2)
             ON CONFLICT(path) DO UPDATE SET resolver_version=excluded.resolver_version",
            params![row.path, MEDIA_RESOLVER_VERSION],
        )
        .map_err(|e| format!("persist media cache metadata failed: {e}"))?;
    Ok(media)
}

fn media_cache_hit(connection: &Connection, row: &ModDto) -> Result<bool, String> {
    connection
        .query_row(
            "SELECT EXISTS(
                SELECT 1
                FROM mod_media_cache c
                LEFT JOIN mod_media_cache_meta m ON m.path = c.path
                WHERE c.path=?1 AND c.size=?2 AND c.modified_ms=?3 AND c.fingerprint=?4
                  AND COALESCE(m.resolver_version, 1)=?5
                  AND (c.icon_url IS NOT NULL OR c.preview_url IS NOT NULL OR c.cached_at_ms>0)
            )",
            params![
                row.path,
                row.size as i64,
                row.modified_ms,
                row.fingerprint as i64,
                MEDIA_RESOLVER_VERSION
            ],
            |value| value.get::<_, i64>(0),
        )
        .map(|value| value != 0)
        .map_err(|e| format!("read media cache state failed: {e}"))
}

fn directory_info_signature(path: &Path, cancelled: &AtomicBool) -> (u64, i64, u64) {
    let mut total_size = 0u64;
    let mut latest_modified = 0i64;
    let mut fingerprint = 1469598103934665603u64;
    let mut files = Vec::new();
    let mut stack = vec![path.to_path_buf()];
    while let Some(current_dir) = stack.pop() {
        if cancelled.load(Ordering::Relaxed) {
            break;
        }
        let Ok(entries) = fs::read_dir(&current_dir) else { continue; };
        for entry in entries.flatten() {
            let current = entry.path();
            let Ok(metadata) = fs::metadata(&current) else { continue; };
            if metadata.is_dir() {
                stack.push(current);
                continue;
            }
            let relative = current
                .strip_prefix(path)
                .unwrap_or(&current)
                .to_string_lossy()
                .replace('\\', "/");
            if !is_mod_info_path(&relative) {
                continue;
            }
            if let Some(modified) = metadata
                .modified()
                .ok()
                .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
                .map(|value| value.as_millis() as i64)
            {
                latest_modified = latest_modified.max(modified);
            }
            total_size = total_size.saturating_add(metadata.len());
            let modified = metadata
                .modified()
                .ok()
                .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
                .map(|value| value.as_millis() as u64)
                .unwrap_or_default();
            files.push((relative, metadata.len(), modified));
        }
    }
    files.sort_by(|left, right| left.0.cmp(&right.0));
    for (relative, size, modified) in files {
        for byte in relative.bytes() {
            fingerprint ^= byte as u64;
            fingerprint = fingerprint.wrapping_mul(1099511628211);
        }
        fingerprint ^= size;
        fingerprint = fingerprint.wrapping_mul(1099511628211);
        fingerprint ^= modified;
        fingerprint = fingerprint.wrapping_mul(1099511628211);
    }
    (total_size, latest_modified, fingerprint)
}

fn is_mod_info_path(path: &str) -> bool {
    let normalized = path.replace('\\', "/").to_ascii_lowercase();
    let name = normalized.rsplit('/').next().unwrap_or_default();
    matches!(
        name,
        "manifest.sii" | "mods_info.sii" | "mod_description.txt" | "description.txt"
    )
}

fn discover_packages(root: Option<&Path>, workshop: bool, cancelled: &AtomicBool) -> Vec<ModDto> {
    discover_packages_with_progress(root, workshop, cancelled, &mut |_, _, _, _, _| {})
}

fn discover_packages_with_progress<F>(
    root: Option<&Path>,
    workshop: bool,
    cancelled: &AtomicBool,
    progress: &mut F,
) -> Vec<ModDto>
where
    F: FnMut(&str, usize, usize, &str, &Path),
{
    let Some(root) = root else {
        return Vec::new();
    };
    let phase = if workshop { "workshop" } else { "local" };
    progress(phase, 0, 0, "", root);
    if !root.is_dir() {
        return Vec::new();
    }
    let Ok(entries) = fs::read_dir(root) else {
        return Vec::new();
    };
    let candidates: Vec<PathBuf> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            let is_dir = path.is_dir();
            let extension = path
                .extension()
                .and_then(|x| x.to_str())
                .unwrap_or_default()
                .to_ascii_lowercase();
            is_dir || extension == "scs" || extension == "zip"
        })
        .collect();
    let total = candidates.len();
    let mut result = Vec::with_capacity(total);
    for (index, path) in candidates.into_iter().enumerate() {
        if cancelled.load(Ordering::Relaxed) {
            break;
        }
        let is_dir = path.is_dir();
        let extension = path
            .extension()
            .and_then(|x| x.to_str())
            .unwrap_or_default()
            .to_ascii_lowercase();
        let id = if workshop {
            path.file_name()
                .and_then(|x| x.to_str())
                .unwrap_or_default()
                .to_string()
        } else {
            path.file_stem()
                .and_then(|x| x.to_str())
                .unwrap_or_default()
                .to_string()
        };
        let display_name = if workshop {
            workshop_cached_title(&id).unwrap_or_else(|| id.replace('_', " "))
        } else {
            id.replace('_', " ")
        };
        // Publish the current package before traversing its files.
        progress(phase, index, total, &display_name, &path);
        let metadata = fs::metadata(&path).ok();
        let (size, modified_ms, fingerprint) = if is_dir {
            directory_info_signature(&path, cancelled)
        } else {
            let size = metadata
                .as_ref()
                .map(|value| value.len())
                .unwrap_or_default();
            let modified_ms = metadata
                .and_then(|value| value.modified().ok())
                .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
                .map(|value| value.as_millis() as i64)
                .unwrap_or_default();
            let fingerprint = size
                .wrapping_mul(1099511628211)
                .wrapping_add(modified_ms as u64);
            (size, modified_ms, fingerprint)
        };
        result.push(ModDto {
            id: id.clone(),
            package_name: id.clone(),
            path: normalize_path(&path),
            package_type: if workshop {
                "workshop".into()
            } else if is_dir {
                "directory".into()
            } else {
                extension
            },
            display_name,
            author: String::new(),
            version: String::new(),
            size,
            modified_ms,
            enabled: false,
            category: "unknown".into(),
            fingerprint,
        });
    }
    progress(phase, result.len(), total, "", root);
    result
}

fn discover_workshop_packages(roots: &[PathBuf], cancelled: &AtomicBool) -> Vec<ModDto> {
    discover_workshop_packages_with_progress(roots, cancelled, &mut |_, _, _, _, _| {})
}

fn discover_workshop_packages_with_progress<F>(
    roots: &[PathBuf],
    cancelled: &AtomicBool,
    progress: &mut F,
) -> Vec<ModDto>
where
    F: FnMut(&str, usize, usize, &str, &Path),
{
    let mut result = Vec::new();
    for root in roots {
        result.extend(discover_packages_with_progress(
            Some(root),
            true,
            cancelled,
            progress,
        ));
        if cancelled.load(Ordering::Relaxed) {
            break;
        }
    }
    result
}

fn load_cached(connection: &Connection) -> Result<Vec<ModDto>, String> {
    let mut statement = connection
        .prepare(
            "SELECT mod_id, package_name, path, package_type, display_name, author, version, size, modified_ms, fingerprint
             FROM mod_package_v2 ORDER BY display_name COLLATE NOCASE, path COLLATE NOCASE",
        )
        .map_err(|e| format!("query mod index failed: {e}"))?;
    let rows = statement
        .query_map([], |row| {
            let id: String = row.get(0)?;
            let package_name: String = row.get(1)?;
            Ok(ModDto {
                id: id.clone(),
                package_name: package_name.clone(),
                path: row.get(2)?,
                package_type: {
                    let stored: String = row.get(3)?;
                    if workshop_id_from_value(&package_name).is_some()
                        || workshop_id_from_value(&id).is_some()
                    {
                        "workshop".into()
                    } else {
                        stored
                    }
                },
                display_name: row.get(4)?,
                author: row.get(5)?,
                version: row.get(6)?,
                size: row.get::<_, i64>(7)?.max(0) as u64,
                modified_ms: row.get(8)?,
                enabled: false,
                category: "unknown".into(),
                fingerprint: row.get::<_, i64>(9)? as u64,
            })
        })
        .map_err(|e| format!("read mod index failed: {e}"))?;
    rows.map(|row| row.map_err(|e| format!("read mod row failed: {e}")))
        .collect()
}

fn refresh_cached_workshop_titles(
    connection: &mut Connection,
    mods: &mut [ModDto],
    paths: &Paths,
) -> Result<(), String> {
    let mut updates = Vec::new();
    for row in mods.iter_mut() {
        if !is_workshop(row) {
            continue;
        }
        let workshop_id = workshop_id_from_value(&row.package_name)
            .or_else(|| workshop_id_from_value(&row.id))
            .unwrap_or_else(|| row.id.clone());
        let title =
            workshop_log_title(&workshop_id, paths).or_else(|| workshop_cached_title(&workshop_id));
        let Some(title) = title else {
            continue;
        };
        if title == row.display_name {
            continue;
        }
        row.display_name = title.clone();
        updates.push((row.path.clone(), title));
    }
    if updates.is_empty() {
        return Ok(());
    }
    let transaction = connection
        .transaction()
        .map_err(|e| format!("begin workshop title refresh failed: {e}"))?;
    for (path, title) in updates {
        transaction
            .execute(
                "UPDATE mod_package_v2 SET display_name = ?1 WHERE path = ?2",
                params![title, path],
            )
            .map_err(|e| format!("persist workshop title failed: {e}"))?;
    }
    transaction
        .commit()
        .map_err(|e| format!("commit workshop title refresh failed: {e}"))?;
    Ok(())
}

fn metadata_needs_refresh(row: &ModDto) -> bool {
    let display = row.display_name.trim();
    let package = row.package_name.trim();
    if display.is_empty() || package.is_empty() {
        return true;
    }
    if !is_workshop(row) {
        let stem = Path::new(&row.path)
            .file_stem()
            .and_then(|value| value.to_str())
            .unwrap_or_default();
        let fallback = stem.replace('_', " ");
        // Rows created before manifest fallback was available often still
        // contain only the filename. Re-read those once so comments and icon
        // fields from encrypted manifests can be persisted.
        if Path::new(&row.path).is_file()
            && !stem.is_empty()
            && (display.eq_ignore_ascii_case(stem) || display.eq_ignore_ascii_case(&fallback))
        {
            return true;
        }
    }
    // Workshop rows discovered before title metadata was persisted can still
    // carry the numeric Workshop ID as their display name. Treat that as stale
    // while a human-readable title is available in the persistent cache.
    is_workshop(row)
        && display.chars().all(|value| value.is_ascii_digit())
        && workshop_cached_title(&row.id).is_some()
}

fn load_metadata_state(connection: &Connection) -> Result<HashMap<String, i64>, String> {
    let mut statement = connection
        .prepare("SELECT path, resolver_version FROM mod_metadata_state")
        .map_err(|e| format!("query metadata state failed: {e}"))?;
    let rows = statement
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
        })
        .map_err(|e| format!("read metadata state failed: {e}"))?;
    rows.map(|row| row.map_err(|e| format!("read metadata state row failed: {e}")))
        .collect()
}

fn ensure_metadata_state_table(connection: &Connection) -> Result<(), String> {
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS mod_metadata_state (
               path TEXT PRIMARY KEY,
               resolver_version INTEGER NOT NULL,
               updated_at_ms INTEGER NOT NULL
             );",
        )
        .map_err(|e| format!("ensure metadata state table failed: {e}"))
}

fn sync_index(connection: &mut Connection, incoming: &[ModDto]) -> Result<ScanSummary, String> {
    sync_index_with_progress(connection, incoming, &mut |_, _, _, _, _| {})
}

fn sync_index_with_progress<F>(
    connection: &mut Connection,
    incoming: &[ModDto],
    progress: &mut F,
) -> Result<ScanSummary, String>
where
    F: FnMut(&str, usize, usize, &str, &Path),
{
    let started = std::time::Instant::now();
    let _write_lock = acquire_db_write_lock();
    ensure_header_state_table(connection)?;
    ensure_mod_index_state_table(connection)?;
    ensure_metadata_state_table(connection)?;
    let cached = load_cached(connection)?;
    let metadata_state = load_metadata_state(connection)?;
    let old: HashMap<String, (i64, u64, u64)> = cached
        .iter()
        .map(|m| (m.path.clone(), (m.modified_ms, m.size, m.fingerprint)))
        .collect();
    let next: HashSet<String> = incoming.iter().map(|m| m.path.clone()).collect();
    let removed = old.keys().filter(|path| !next.contains(*path)).count();
    let mut added = 0;
    let mut updated = 0;
    let tx = connection
        .transaction()
        .map_err(|e| format!("begin index transaction failed: {e}"))?;
    for path in old.keys().filter(|path| !next.contains(*path)) {
        tx.execute("DELETE FROM mod_package_v2 WHERE path = ?1", params![path])
            .map_err(|e| format!("remove stale index row failed: {e}"))?;
        tx.execute(
            "DELETE FROM mod_package_header_state WHERE path = ?1",
            params![path],
        )
        .map_err(|e| format!("remove stale header state failed: {e}"))?;
        tx.execute(
            "DELETE FROM mod_metadata_state WHERE path = ?1",
            params![path],
        )
        .map_err(|e| format!("remove stale metadata state failed: {e}"))?;
    }
    for (index, mod_row) in incoming.iter().enumerate() {
        let changed = old
            .get(&mod_row.path)
            .map(|(modified, size, fingerprint)| {
                *modified != mod_row.modified_ms
                    || *size != mod_row.size
                    || *fingerprint != mod_row.fingerprint
            })
            .unwrap_or(true);
        let previous = cached.iter().find(|row| row.path == mod_row.path);
        let metadata_refresh_needed = previous.is_some_and(metadata_needs_refresh)
            && metadata_state.get(&mod_row.path).copied() != Some(METADATA_RESOLVER_VERSION);
        if !changed && !metadata_refresh_needed {
            progress(
                "cached",
                index + 1,
                incoming.len(),
                &previous.unwrap().display_name,
                Path::new(&mod_row.path),
            );
            continue;
        }
        if changed && old.contains_key(&mod_row.path) {
            updated += 1;
        } else if !old.contains_key(&mod_row.path) {
            added += 1;
        }
        let mut enriched = mod_row.clone();
        // Manifest parsing can be expensive for large SCS archives.
        progress(
            "metadata",
            index,
            incoming.len(),
            previous
                .map(|row| row.display_name.as_str())
                .unwrap_or(&mod_row.display_name),
            Path::new(&mod_row.path),
        );
        let (package_name, display_name, author, version, _icon_filename) =
            manifest_for(Path::new(&mod_row.path));
        if !package_name.is_empty() {
            enriched.package_name = package_name;
        } else if let Some(previous) = previous {
            enriched.package_name = previous.package_name.clone();
        }
        if !display_name.is_empty() {
            enriched.display_name = display_name;
        } else if is_workshop(&enriched) {
            if let Some(title) = workshop_cached_title(&enriched.id) {
                enriched.display_name = title;
            } else if let Some(previous) = previous {
                enriched.display_name = previous.display_name.clone();
            }
        } else if let Some(previous) = previous {
            enriched.display_name = previous.display_name.clone();
        }
        if is_workshop(&enriched)
            && (enriched.display_name.trim().is_empty()
                || enriched.display_name.trim() == enriched.id.trim()
                || enriched
                    .display_name
                    .trim()
                    .chars()
                    .all(|value| value.is_ascii_digit()))
        {
            if let Some(title) = workshop_cached_title(&enriched.id) {
                enriched.display_name = title;
            }
        }
        enriched.author = if author.is_empty() {
            previous.map(|row| row.author.clone()).unwrap_or_default()
        } else {
            author
        };
        enriched.version = if version.is_empty() {
            previous.map(|row| row.version.clone()).unwrap_or_default()
        } else {
            version
        };
        tx.execute(
            "INSERT INTO mod_package_v2(path, mod_id, package_name, package_type, display_name, author, version, size, modified_ms, fingerprint, scanned_at_ms)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)
             ON CONFLICT(path) DO UPDATE SET mod_id=excluded.mod_id,
               package_name=excluded.package_name, package_type=excluded.package_type,
               display_name=excluded.display_name, author=excluded.author, version=excluded.version,
               size=excluded.size, modified_ms=excluded.modified_ms, fingerprint=excluded.fingerprint,
               scanned_at_ms=excluded.scanned_at_ms",
            params![
                enriched.path,
                enriched.id,
                enriched.package_name,
                enriched.package_type,
                enriched.display_name,
                enriched.author,
                enriched.version,
                enriched.size as i64,
                enriched.modified_ms,
                enriched.fingerprint as i64,
                now_ms()
            ],
        )
        .map_err(|e| format!("write mod index failed: {e}"))?;
        tx.execute(
            "INSERT INTO mod_metadata_state(path, resolver_version, updated_at_ms)
             VALUES (?1, ?2, ?3)
             ON CONFLICT(path) DO UPDATE SET resolver_version=excluded.resolver_version,
               updated_at_ms=excluded.updated_at_ms",
            params![mod_row.path, METADATA_RESOLVER_VERSION, now_ms()],
        )
        .map_err(|e| format!("write metadata state failed: {e}"))?;
        // Persist exactly the same shallow signature used by startup
        // comparison. Directory rows use the info-file aggregate returned by
        // `directory_info_signature`, so game assets do not invalidate them.
        let header_size = enriched.size;
        let header_modified_ms = enriched.modified_ms;
        // Workshop packages are directories too, but use the "workshop"
        // package type. Persist the physical type rather than inferring it
        // from the display classification, otherwise every Workshop item is
        // treated as changed on the next startup.
        let header_is_directory = Path::new(&enriched.path).is_dir();
        tx.execute(
            "INSERT INTO mod_package_header_state(path,size,modified_ms,is_directory)
             VALUES (?1,?2,?3,?4)
             ON CONFLICT(path) DO UPDATE SET size=excluded.size, modified_ms=excluded.modified_ms, is_directory=excluded.is_directory",
            params![
                enriched.path,
                header_size as i64,
                header_modified_ms,
                if header_is_directory { 1 } else { 0 }
            ],
        )
        .map_err(|e| format!("write mod header state failed: {e}"))?;
    }
    tx.execute(
        "DELETE FROM mod_media_cache WHERE path NOT IN (SELECT path FROM mod_package_v2)",
        [],
    )
    .map_err(|e| format!("remove stale media cache failed: {e}"))?;
    tx.execute(
        "DELETE FROM mod_media_cache_meta WHERE path NOT IN (SELECT path FROM mod_package_v2)",
        [],
    )
    .map_err(|e| format!("remove stale media cache metadata failed: {e}"))?;
    progress("persist", incoming.len(), incoming.len(), "", Path::new(""));
    tx.commit()
        .map_err(|e| format!("commit mod index failed: {e}"))?;
    connection
        .execute(
            "INSERT INTO mod_index_state(id,completed_at_ms,package_count)
             VALUES (1,?1,?2)
             ON CONFLICT(id) DO UPDATE SET completed_at_ms=excluded.completed_at_ms, package_count=excluded.package_count",
            params![now_ms(), incoming.len() as i64],
        )
        .map_err(|e| format!("persist mod index state failed: {e}"))?;
    progress(
        "complete",
        incoming.len(),
        incoming.len(),
        "",
        Path::new(""),
    );
    Ok(ScanSummary {
        total: incoming.len(),
        added,
        updated,
        removed,
        inspected: added + updated,
        elapsed_ms: started.elapsed().as_millis(),
    })
}

fn package_fingerprint(path: &Path) -> (String, i64, i64) {
    if path.is_dir() {
        let mut hash = 1469598103934665603u64;
        let mut stack = fs::read_dir(path)
            .ok()
            .into_iter()
            .flat_map(|entries| entries.flatten().map(|entry| entry.path()))
            .filter(|entry| {
                entry.is_dir()
                    && entry
                        .file_name()
                        .and_then(|value| value.to_str())
                        .is_some_and(|name| {
                            name.eq_ignore_ascii_case("def") || name.eq_ignore_ascii_case("locale")
                        })
            })
            .collect::<Vec<_>>();
        let mut files = Vec::new();
        while let Some(current) = stack.pop() {
            let Ok(metadata) = fs::metadata(&current) else {
                continue;
            };
            if metadata.is_dir() {
                if let Ok(entries) = fs::read_dir(&current) {
                    for entry in entries.flatten() {
                        stack.push(entry.path());
                    }
                }
                continue;
            }
            let normalized = current
                .strip_prefix(path)
                .unwrap_or(&current)
                .to_string_lossy()
                .replace('\\', "/");
            if !is_localization_path(&normalized) && !is_definition_path(&normalized) {
                continue;
            }
            files.push((normalized, metadata));
        }
        files.sort_by(|left, right| left.0.cmp(&right.0));
        for (normalized, metadata) in files {
            for byte in normalized.bytes() {
                hash ^= byte as u64;
                hash = hash.wrapping_mul(1099511628211);
            }
            hash ^= metadata.len();
            hash = hash.wrapping_mul(1099511628211);
            let modified = metadata
                .modified()
                .ok()
                .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
                .map(|duration| duration.as_millis() as u64)
                .unwrap_or_default();
            hash ^= modified;
            hash = hash.wrapping_mul(1099511628211);
        }
        return ("directory".into(), 0, hash as i64);
    }
    let metadata = fs::metadata(path).ok();
    let size = metadata
        .as_ref()
        .map(|value| value.len())
        .unwrap_or_default() as i64;
    let modified = metadata
        .and_then(|value| value.modified().ok())
        .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
        .map(|duration| duration.as_millis() as i64)
        .unwrap_or_default();
    let kind = path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("file")
        .to_ascii_lowercase();
    // ZIP central-directory metadata lets us invalidate the snapshot when
    // def/locale entries change without hashing or extracting game assets.
    if kind == "zip" || kind == "scs" {
        if let Ok(file) = fs::File::open(path) {
            if let Ok(mut archive) = ZipArchive::new(file) {
                let mut hash = 1469598103934665603u64;
                let mut matched = false;
                for index in 0..archive.len() {
                    let Ok(entry) = archive.by_index(index) else { continue; };
                    let normalized = entry.name().replace('\\', "/");
                    if !is_localization_path(&normalized) && !is_definition_path(&normalized) {
                        continue;
                    }
                    matched = true;
                    for byte in normalized.bytes() {
                        hash ^= byte as u64;
                        hash = hash.wrapping_mul(1099511628211);
                    }
                    hash ^= entry.size();
                    hash = hash.wrapping_mul(1099511628211);
                    hash ^= entry.crc32() as u64;
                    hash = hash.wrapping_mul(1099511628211);
                }
                if matched {
                    return ("archive-l10n-v5".into(), size, (hash ^ modified as u64) as i64);
                }
            }
        }
    }
    (format!("{kind}-l10n-v5"), size, modified)
}

fn has_path_segment(path: &str, segment: &str) -> bool {
    path.split('/')
        .any(|part| part.eq_ignore_ascii_case(segment))
}

fn is_localization_path(path: &str) -> bool {
    let normalized = path.replace('\\', "/").to_ascii_lowercase();
    (normalized.ends_with(".sii") || normalized.ends_with(".sui"))
        && has_path_segment(&normalized, "locale")
}

fn is_definition_path(path: &str) -> bool {
    let normalized = path.replace('\\', "/").to_ascii_lowercase();
    (normalized.ends_with(".sii") || normalized.ends_with(".sui"))
        && has_path_segment(&normalized, "def")
}

fn is_localization_source_path(path: &str) -> bool {
    let relative = path
        .rsplit_once("::")
        .map(|(_, value)| value)
        .unwrap_or(path);
    is_localization_path(relative) || is_definition_path(relative)
}

fn is_localization_source_path_for_locale(path: &str, locale: &str) -> bool {
    let relative = path
        .rsplit_once("::")
        .map(|(_, value)| value)
        .unwrap_or(path);
    if is_definition_path(relative) {
        return true;
    }
    let normalized = relative.replace('\\', "/");
    let parts = normalized.split('/').collect::<Vec<_>>();
    let locale_lower = locale.to_ascii_lowercase();
    parts.windows(2).any(|window| {
        window[0].eq_ignore_ascii_case("locale")
            && window[1].eq_ignore_ascii_case(&locale_lower)
            && (normalized.to_ascii_lowercase().ends_with(".sii")
                || normalized.to_ascii_lowercase().ends_with(".sui"))
    })
}

fn category_for_path(path: &str) -> String {
    let value = path.to_ascii_lowercase();
    if value.contains("country") {
        "country".into()
    } else if value.contains("ferry") {
        "ferry".into()
    } else if value.contains("city") {
        "city".into()
    } else if value.contains("tips") || value.contains("tip") || value.contains("hint") {
        "tips".into()
    } else {
        "unknown".into()
    }
}

fn category_for_localization_key(key: &str, fallback: &str) -> String {
    let normalized = key.trim().to_ascii_lowercase();
    if normalized.starts_with("city.") || normalized.starts_with("city_") {
        "city".into()
    } else if normalized.starts_with("country.") || normalized.starts_with("country_") {
        "country".into()
    } else if normalized.starts_with("ferry.") || normalized.starts_with("ferry_") {
        "ferry".into()
    } else if normalized.starts_with("tip.")
        || normalized.starts_with("tips.")
        || normalized.starts_with("hint.")
    {
        "tips".into()
    } else {
        fallback.to_string()
    }
}

fn quoted_value(value: &str) -> String {
    let trimmed = value.trim().trim_end_matches(',');
    let Some(start) = trimmed.find('"') else {
        return trimmed.to_string();
    };
    let Some(end) = trimmed[start + 1..].rfind('"') else {
        return trimmed[start + 1..].to_string();
    };
    unescape_sii(&trimmed[start + 1..start + 1 + end])
}

fn parse_localization_text(
    text: &str,
    source_path: &str,
    package_name: &str,
    category: &str,
) -> Vec<LocalizationEntryDto> {
    fn parse_unit(
        unit_text: &str,
        source_path: &str,
        package_name: &str,
        category: &str,
    ) -> Vec<LocalizationEntryDto> {
        // SII accepts key[], key[0], and other indexed array spellings.
        // Pair arrays inside each localization_db unit so entries from
        // separate units can never be accidentally cross-matched.
        let mut keys: Vec<(Option<usize>, String)> = Vec::new();
        let mut values: Vec<(Option<usize>, String)> = Vec::new();
        let mut scalar = Vec::new();
        let array = Regex::new(r#"(?i)\b(key|val)\s*\[\s*(\d*)\s*\]\s*:\s*"((?:\\.|[^"\\])*)""#)
            .expect("localization array regex");
        for capture in array.captures_iter(unit_text) {
            let name = capture
                .get(1)
                .map(|value| value.as_str())
                .unwrap_or_default();
            let index = capture
                .get(2)
                .map(|value| value.as_str())
                .filter(|value| !value.is_empty())
                .and_then(|value| value.parse::<usize>().ok());
            let value = capture
                .get(3)
                .map(|value| unescape_sii(value.as_str()))
                .unwrap_or_default();
            if name.eq_ignore_ascii_case("key") {
                keys.push((index, value));
            } else {
                values.push((index, value));
            }
        }
        for line in unit_text.lines() {
            let trimmed = line.trim();
            let Some((raw_key, raw_value)) = trimmed.split_once(':') else {
                continue;
            };
            let key = raw_key.trim();
            let value = quoted_value(raw_value);
            if key.to_ascii_lowercase().starts_with("key[")
                || key.to_ascii_lowercase().starts_with("val[")
            {
                continue;
            } else if !key.contains("[]")
                && !key.contains(' ')
                && !key.eq_ignore_ascii_case("active_mods")
                && !key.eq_ignore_ascii_case("SiiNunit")
                && !key.eq_ignore_ascii_case("localization_db")
            {
                scalar.push((key.to_string(), value));
            }
        }

        let mut output = Vec::new();
        for (position, (key_index, key)) in keys.into_iter().enumerate() {
            let value = key_index
                .and_then(|index| {
                    values
                        .iter()
                        .find(|(value_index, _)| *value_index == Some(index))
                        .map(|(_, value)| value.clone())
                })
                .or_else(|| values.get(position).map(|(_, value)| value.clone()))
                .unwrap_or_default();
            let entry_category = category_for_localization_key(&key, category);
            output.push(LocalizationEntryDto {
                key: key.clone(),
                value: value.clone(),
                source_name: String::new(),
                source_path: source_path.to_string(),
                package_name: package_name.to_string(),
                category: entry_category,
                status: if value.is_empty() {
                    "missing_value".into()
                } else {
                    "native".into()
                },
                locale_key_present: true,
                def_locale_key_present: false,
                unit_name: String::new(),
                locale_key: key,
            });
        }
        for (key, value) in scalar {
            if key.eq_ignore_ascii_case("name")
                || key.eq_ignore_ascii_case("city_name")
                || key.eq_ignore_ascii_case("country_name")
                || key.eq_ignore_ascii_case("ferry_name")
            {
                continue;
            }
            let entry_category = category_for_localization_key(&key, category);
            output.push(LocalizationEntryDto {
                key: key.clone(),
                value: value.clone(),
                source_name: String::new(),
                source_path: source_path.to_string(),
                package_name: package_name.to_string(),
                category: entry_category,
                status: if value.is_empty() {
                    "missing_value".into()
                } else {
                    "native".into()
                },
                locale_key_present: true,
                def_locale_key_present: false,
                unit_name: String::new(),
                locale_key: key,
            });
        }
        output
    }

    // Most locale files contain multiple localization_db units. The body
    // regex intentionally stops at the unit's closing brace; locale units do
    // not contain nested braces in their key/value payloads.
    let unit = Regex::new(r"(?is)(?:localization_db|localization)\s*:\s*[^{]+\{(.*?)\}")
        .expect("localization unit regex");
    let mut output = Vec::new();
    for capture in unit.captures_iter(text) {
        if let Some(body) = capture.get(1) {
            output.extend(parse_unit(
                body.as_str(),
                source_path,
                package_name,
                category,
            ));
        }
    }
    if !output.is_empty() {
        return output;
    }

    // A few community files omit the unit header; retain a permissive
    // fallback so those files still contribute locale entries.
    parse_unit(text, source_path, package_name, category)
}

fn parse_definition_text(
    text: &str,
    source_path: &str,
    package_name: &str,
    category: &str,
) -> Vec<LocalizationEntryDto> {
    let block =
        Regex::new(r"(?is)(city_data|country_data|ferry_data|tip_data|tips_data|hint_data)\s*:\s*([A-Za-z0-9_.-]+)\s*\{(.*?)\}")
            .expect("definition regex");
    let field = Regex::new(
        r#"(?m)(city_name|city_name_localized|country_name|country_name_localized|name|name_localized|ferry_name|ferry_name_localized)\s*:\s*"((?:\\.|[^"\\])*)""#,
    )
    .expect("definition field regex");
    let mut output = Vec::new();
    for unit in block.captures_iter(text) {
        let type_name = unit.get(1).map(|value| value.as_str()).unwrap_or_default();
        let unit_name = unit.get(2).map(|value| value.as_str()).unwrap_or_default();
        let body = unit.get(3).map(|value| value.as_str()).unwrap_or_default();
        let mut fields = HashMap::new();
        for capture in field.captures_iter(body) {
            let name = capture
                .get(1)
                .map(|value| value.as_str())
                .unwrap_or_default();
            let value = capture
                .get(2)
                .map(|value| unescape_sii(value.as_str()))
                .unwrap_or_default();
            fields.insert(name.to_ascii_lowercase(), value);
        }
        let source_field = match type_name.to_ascii_lowercase().as_str() {
            "city_data" => "city_name",
            "country_data" => "country_name",
            "ferry_data" => "ferry_name",
            "tip_data" | "tips_data" | "hint_data" => "name",
            _ => "name",
        };
        let localized_field = match type_name.to_ascii_lowercase().as_str() {
            "city_data" => "city_name_localized",
            "country_data" => "country_name_localized",
            "ferry_data" => "ferry_name_localized",
            "tip_data" | "tips_data" | "hint_data" => "name_localized",
            _ => "name_localized",
        };
        let source = fields
            .get(source_field)
            .cloned()
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| unit_name.to_string());
        let localized = fields.get(localized_field).cloned().unwrap_or_default();
        let wrapped =
            localized.starts_with("@@") && localized.ends_with("@@") && localized.len() > 4;
        let key = if wrapped {
            localized[2..localized.len() - 2].to_string()
        } else {
            source.clone()
        };
        if key.trim().is_empty() {
            continue;
        }
        let value = if wrapped { String::new() } else { localized };
        output.push(LocalizationEntryDto {
            key: key.clone(),
            value: value.clone(),
            source_name: source.clone(),
            source_path: source_path.to_string(),
            package_name: package_name.to_string(),
            category: category.to_string(),
            status: if value.is_empty() {
                "missing_locale".into()
            } else {
                "native".into()
            },
            locale_key_present: false,
            def_locale_key_present: fields.contains_key(localized_field),
            unit_name: unit_name.to_string(),
            locale_key: key,
        });
    }
    output
}

fn scan_localization_directory(
    root: &Path,
    source_root: &Path,
    package_name: &str,
    locale: &str,
    cancelled: &AtomicBool,
    #[cfg(feature = "desktop")] app: Option<&AppHandle>,
) -> Vec<LocalizationEntryDto> {
    let mut output = Vec::new();
    let Ok(files) = fs::read_dir(root) else {
        return output;
    };
    let mut stack: Vec<PathBuf> = files.flatten().map(|entry| entry.path()).collect();
    stack.sort_by(|left, right| right.cmp(left));
    while let Some(path) = stack.pop() {
        if cancelled.load(Ordering::Relaxed) {
            break;
        }
        if path.is_dir() {
            let relative_dir = path
                .strip_prefix(root)
                .unwrap_or(&path)
                .to_string_lossy()
                .replace('\\', "/");
            let segments = relative_dir
                .split('/')
                .filter(|value| !value.is_empty())
                .collect::<Vec<_>>();
            let allowed = segments.is_empty()
                || segments[0].eq_ignore_ascii_case("def")
                || (segments[0].eq_ignore_ascii_case("locale")
                    && (segments.len() == 1 || segments[1].eq_ignore_ascii_case(locale)));
            if !allowed {
                continue;
            }
            if let Ok(entries) = fs::read_dir(&path) {
                let mut children: Vec<PathBuf> =
                    entries.flatten().map(|entry| entry.path()).collect();
                children.sort_by(|left, right| right.cmp(left));
                stack.extend(children);
            }
            continue;
        }
        let relative = path
            .strip_prefix(root)
            .unwrap_or(&path)
            .to_string_lossy()
            .replace('\\', "/");
        if !is_localization_source_path_for_locale(&relative, locale) {
            continue;
        }
        let Ok(text) = fs::read_to_string(&path) else {
            continue;
        };
        let source_path = format!("{}::{}", source_root.display(), relative);
        #[cfg(feature = "desktop")]
        if let Some(app) = app {
            let _ = app.emit(
                "localization-file-progress",
                serde_json::json!({"packageName": package_name, "file": relative}),
            );
        }
        if is_definition_path(&relative) {
            output.extend(parse_definition_text(
                &text,
                &source_path,
                package_name,
                &category_for_path(&relative),
            ));
        } else {
            output.extend(parse_localization_text(
                &text,
                &source_path,
                package_name,
                &category_for_path(&relative),
            ));
        }
    }
    output
}

fn scan_localization_archive(
    path: &Path,
    locale: &str,
    cancelled: &AtomicBool,
    #[cfg(feature = "desktop")] app: Option<&AppHandle>,
) -> Vec<LocalizationEntryDto> {
    let mut output = Vec::new();
    let Ok(file) = fs::File::open(path) else {
        return output;
    };
    let Ok(mut archive) = ZipArchive::new(file) else {
        return output;
    };
    let package_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    for index in 0..archive.len() {
        if cancelled.load(Ordering::Relaxed) {
            break;
        }
        let Ok(mut entry) = archive.by_index(index) else {
            continue;
        };
        if entry.name().ends_with('/') {
            continue;
        }
        let normalized = entry.name().replace('\\', "/");
        if !is_localization_source_path_for_locale(&normalized, locale) {
            continue;
        }
        let mut bytes = Vec::new();
        if entry.read_to_end(&mut bytes).is_err() {
            continue;
        }
        let text = String::from_utf8_lossy(&bytes);
        let source_path = format!("{}::{}", path.display(), normalized);
        #[cfg(feature = "desktop")]
        if let Some(app) = app {
            let _ = app.emit(
                "localization-file-progress",
                serde_json::json!({"packageName": package_name, "file": normalized}),
            );
        }
        if is_definition_path(&normalized) {
            output.extend(parse_definition_text(
                &text,
                &source_path,
                package_name,
                &category_for_path(&normalized),
            ));
        } else {
            output.extend(parse_localization_text(
                &text,
                &source_path,
                package_name,
                &category_for_path(&normalized),
            ));
        }
    }
    output
}

fn scan_external_localization_archive(
    path: &Path,
    locale: &str,
    cancelled: &AtomicBool,
    #[cfg(feature = "desktop")] app: Option<&AppHandle>,
) -> Vec<LocalizationEntryDto> {
    let Some(temp) = extractor_temp_directory(path, &format!("localization-{locale}")) else {
        return Vec::new();
    };
    let partial = format!("/def,/locale/{locale}");
    let (tool, args): (PathBuf, Vec<String>) = match archive_kind(path) {
        archive_core::ArchiveKind::HashFs => {
            let Some(extractor) = extractor_path() else {
                let _ = fs::remove_dir_all(&temp);
                return Vec::new();
            };
            let mut args = vec![
                external_tool_path(path).to_string_lossy().into_owned(),
                format!("--partial={partial}"),
                "-d".into(),
                temp.to_string_lossy().into_owned(),
                "-s".into(),
            ];
            if !hashfs_has_top_level_directory(path) {
                args.insert(1, "--deep".into());
            }
            (extractor, args)
        }
        archive_core::ArchiveKind::Aem => {
            let Some(sxc) = sxc_path() else {
                let _ = fs::remove_dir_all(&temp);
                return Vec::new();
            };
            (
                sxc,
                vec![
                    external_tool_path(path).to_string_lossy().into_owned(),
                    "-o".into(),
                    temp.to_string_lossy().into_owned(),
                    "-f".into(),
                    partial,
                    "-q".into(),
                ],
            )
        }
        _ => {
            let _ = fs::remove_dir_all(&temp);
            return Vec::new();
        }
    };
    #[cfg(feature = "desktop")]
    if let Some(app) = app {
        let _ = app.emit(
            "localization-file-progress",
            serde_json::json!({
                "packageName": path.file_name().and_then(|value| value.to_str()).unwrap_or_default(),
                "file": format!("定位 def/locale/{locale}"),
            }),
        );
    }
    if !run_external_command_cancellable(&tool, &args, cancelled) {
        app_log(
            "WARN",
            &format!("archive extract failed: {}", path.display()),
        );
        let _ = fs::remove_dir_all(&temp);
        return Vec::new();
    }
    let package_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    let result = scan_localization_directory(
        &temp,
        path,
        package_name,
        locale,
        cancelled,
        #[cfg(feature = "desktop")]
        app,
    );
    let _ = fs::remove_dir_all(&temp);
    result
}

fn scan_localization_package(
    path: &Path,
    locale: &str,
    cancelled: &AtomicBool,
    #[cfg(feature = "desktop")] app: Option<&AppHandle>,
) -> Vec<LocalizationEntryDto> {
    if path.is_dir() {
        return scan_localization_directory(
            path,
            path,
            path.file_name()
                .and_then(|value| value.to_str())
                .unwrap_or_default(),
            locale,
            cancelled,
            #[cfg(feature = "desktop")]
            app,
        );
    }
    if path
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|value| value.eq_ignore_ascii_case("sii") || value.eq_ignore_ascii_case("sui"))
    {
        let Ok(text) = fs::read_to_string(path) else {
            return Vec::new();
        };
        let package_name = path
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or_default();
        let source_path = path.display().to_string();
        if !is_localization_source_path_for_locale(&source_path, locale) {
            return Vec::new();
        }
        #[cfg(feature = "desktop")]
        if let Some(app) = app {
            let file_name = path
                .file_name()
                .and_then(|value| value.to_str())
                .unwrap_or_default();
            let _ = app.emit(
                "localization-file-progress",
                serde_json::json!({"packageName": package_name, "file": file_name}),
            );
        }
        return if is_definition_path(&source_path) {
            parse_definition_text(
                &text,
                &source_path,
                package_name,
                &category_for_path(&source_path),
            )
        } else {
            parse_localization_text(
                &text,
                &source_path,
                package_name,
                &category_for_path(&source_path),
            )
        };
    }
    match archive_kind(path) {
        archive_core::ArchiveKind::Zip => scan_localization_archive(
            path,
            locale,
            cancelled,
            #[cfg(feature = "desktop")]
            app,
        ),
        archive_core::ArchiveKind::HashFs | archive_core::ArchiveKind::Aem => {
            scan_external_localization_archive(
                path,
                locale,
                cancelled,
                #[cfg(feature = "desktop")]
                app,
            )
        }
        archive_core::ArchiveKind::Unknown => Vec::new(),
    }
}

fn load_localization_snapshot(
    connection: &Connection,
    path: &str,
    locale: &str,
    fingerprint: &(String, i64, i64),
) -> Result<Option<Vec<LocalizationEntryDto>>, String> {
    let metadata = connection
        .query_row(
            "SELECT package_type, file_size, modified_ms FROM localization_package_v2
             WHERE package_path = ?1 AND target_locale = ?2",
            params![path, locale],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, i64>(1)?,
                    row.get::<_, i64>(2)?,
                ))
            },
        )
        .optional()
        .map_err(|error| format!("read localization snapshot failed: {error}"))?;
    if metadata.as_ref() != Some(fingerprint) {
        return Ok(None);
    }
    let mut statement = connection
        .prepare(
            "SELECT key, value, source_name, source_path, package_name, category, status,
                    locale_key_present, def_locale_key_present, unit_name, locale_key
             FROM localization_entry_v2
             WHERE package_path = ?1 AND target_locale = ?2
             ORDER BY entry_order",
        )
        .map_err(|error| format!("prepare localization snapshot failed: {error}"))?;
    let rows = statement
        .query_map(params![path, locale], |row| {
            let mut entry = LocalizationEntryDto {
                key: row.get(0)?,
                value: row.get(1)?,
                source_name: row.get(2)?,
                source_path: row.get(3)?,
                package_name: row.get(4)?,
                category: row.get(5)?,
                status: row.get(6)?,
                locale_key_present: row.get::<_, i64>(7)? != 0,
                def_locale_key_present: row.get::<_, i64>(8)? != 0,
                unit_name: row.get(9)?,
                locale_key: row.get(10)?,
            };
            // Rewrite the source path on load so entries cached from the
            // old extractor temp directory (ets2mm-preview-*) resolve to the
            // real package path and stay stable across restarts.
            let relative = entry
                .source_path
                .rsplit_once("::")
                .map(|(_, value)| value)
                .unwrap_or_default()
                .to_string();
            if entry.source_path.contains("ets2mm-preview") {
                entry.source_path = format!("{}::{}", path, relative);
            }
            Ok(entry)
        })
        .map_err(|error| format!("read localization snapshot failed: {error}"))?;
    rows.map(|row| row.map_err(|error| format!("read localization entry failed: {error}")))
        .collect::<Result<Vec<_>, _>>()
        .map(Some)
}

fn save_localization_snapshots(
    connection: &mut Connection,
    locale: &str,
    current_paths: &[String],
    snapshots: &[(String, (String, i64, i64), Vec<LocalizationEntryDto>)],
) -> Result<(), String> {
    save_localization_snapshots_inner(connection, locale, current_paths, snapshots, true)
}

fn save_localization_snapshot(
    connection: &mut Connection,
    locale: &str,
    snapshot: &(String, (String, i64, i64), Vec<LocalizationEntryDto>),
) -> Result<(), String> {
    // Incremental package commits must not run stale cleanup. During a scan,
    // the current package is only one member of the complete package set; if
    // cleanup ran here it would delete snapshots already persisted for the
    // preceding packages.
    save_localization_snapshots_inner(
        connection,
        locale,
        &[],
        std::slice::from_ref(snapshot),
        false,
    )
}

fn save_localization_snapshots_inner(
    connection: &mut Connection,
    locale: &str,
    current_paths: &[String],
    snapshots: &[(String, (String, i64, i64), Vec<LocalizationEntryDto>)],
    cleanup_stale: bool,
) -> Result<(), String> {
    let _write_lock = acquire_db_write_lock();
    let transaction = connection
        .transaction()
        .map_err(|error| format!("begin localization transaction failed: {error}"))?;
    for (path, fingerprint, entries) in snapshots {
        transaction
            .execute(
                "DELETE FROM localization_entry_v2 WHERE package_path = ?1 AND target_locale = ?2",
                params![path, locale],
            )
            .map_err(|error| format!("clear localization entries failed: {error}"))?;
        transaction
            .execute(
                "INSERT INTO localization_package_v2
                 (package_path, target_locale, package_type, file_size, modified_ms, scanned_at_ms)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)
                 ON CONFLICT(package_path, target_locale) DO UPDATE SET
                   package_type=excluded.package_type, file_size=excluded.file_size,
                   modified_ms=excluded.modified_ms, scanned_at_ms=excluded.scanned_at_ms",
                params![
                    path,
                    locale,
                    fingerprint.0,
                    fingerprint.1,
                    fingerprint.2,
                    now_ms()
                ],
            )
            .map_err(|error| format!("write localization package snapshot failed: {error}"))?;
        for (index, entry) in entries.iter().enumerate() {
            transaction
                .execute(
                    "INSERT INTO localization_entry_v2
                     (package_path, target_locale, entry_order, key, value, source_name, source_path,
                      package_name, category, status, locale_key_present,
                      def_locale_key_present, unit_name, locale_key)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
                    params![
                        path,
                        locale,
                        index as i64,
                        entry.key,
                        entry.value,
                        entry.source_name,
                        entry.source_path,
                        entry.package_name,
                        entry.category,
                        entry.status,
                        if entry.locale_key_present { 1 } else { 0 },
                        if entry.def_locale_key_present { 1 } else { 0 },
                        entry.unit_name,
                        entry.locale_key
                    ],
                )
                .map_err(|error| format!("write localization entry snapshot failed: {error}"))?;
        }
    }
    if cleanup_stale {
        let current_paths: HashSet<&str> = current_paths.iter().map(String::as_str).collect();
        let mut stale = Vec::new();
        {
            let mut statement = transaction
                .prepare(
                    "SELECT package_path FROM localization_package_v2
                     WHERE target_locale = ?1",
                )
                .map_err(|error| format!("read stale localization snapshots failed: {error}"))?;
            let rows = statement
                .query_map(params![locale], |row| row.get::<_, String>(0))
                .map_err(|error| format!("read stale localization snapshots failed: {error}"))?;
            for row in rows {
                let path = row
                    .map_err(|error| format!("read stale localization snapshot failed: {error}"))?;
                if !current_paths.contains(path.as_str()) {
                    stale.push(path);
                }
            }
        }
        for path in stale {
            transaction
                .execute(
                    "DELETE FROM localization_entry_v2 WHERE package_path = ?1 AND target_locale = ?2",
                    params![path, locale],
                )
                .map_err(|error| format!("remove stale localization entries failed: {error}"))?;
            transaction
                .execute(
                    "DELETE FROM localization_package_v2 WHERE package_path = ?1 AND target_locale = ?2",
                    params![path, locale],
                )
                .map_err(|error| format!("remove stale localization snapshot failed: {error}"))?;
        }
    }
    transaction
        .commit()
        .map_err(|error| format!("commit localization snapshots failed: {error}"))?;
    Ok(())
}

fn assign_missing_locale_keys(packages: &mut [Vec<LocalizationEntryDto>]) {
    // 收集所有已占用的 localization key（来自 locale 词条与已带 localized 的 def 词条）。
    // 生成的 key 必须与它们全局不冲突，否则优先级的覆盖/合并会张冠李戴。
    let mut used: HashSet<String> = HashSet::new();
    for package in packages.iter() {
        for entry in package.iter() {
            if !entry.def_locale_key_present && !entry.locale_key_present {
                continue;
            }
            let key = entry.locale_key.trim();
            if !key.is_empty() {
                used.insert(key.to_ascii_lowercase());
            }
        }
    }

    // 为缺少 localized 字段的 def 词条生成全局唯一 key：
    //   - name 为英文时用 name 作为 key，冲突则追加 _2/_3 后缀；
    //   - name 为非英文（Cyrillic/CJK 等）时改用 __manual_N 占位，交由用户在界面上填写真实 key。
    let mut manual_counter = 0usize;
    for package in packages.iter_mut() {
        for entry in package.iter_mut() {
            if entry.def_locale_key_present
                || entry.locale_key_present
                || entry.source_name.trim().is_empty()
            {
                continue;
            }
            let name = entry.source_name.trim();
            if name.chars().all(|character| character.is_ascii()) {
                let mut key = name.to_string();
                let mut suffix = 2usize;
                while used.contains(&key.to_ascii_lowercase()) {
                    key = format!("{name}_{suffix}");
                    suffix += 1;
                }
                used.insert(key.to_ascii_lowercase());
                entry.key = key.clone();
                entry.locale_key = key;
            } else {
                manual_counter += 1;
                let key = format!("__manual_{manual_counter}");
                entry.key = key.clone();
                entry.locale_key = key;
            }
        }
    }
}

fn merge_localization_entries(
    packages: impl IntoIterator<Item = Vec<LocalizationEntryDto>>,
) -> Vec<LocalizationEntryDto> {
    let mut result: Vec<LocalizationEntryDto> = Vec::new();
    let mut positions: HashMap<String, usize> = HashMap::new();
    for entries in packages {
        for entry in entries {
            let merge_key = entry.key.to_ascii_lowercase();
            if let Some(index) = positions.get(&merge_key).copied() {
                if result[index].package_name == entry.package_name {
                    // Definitions and locale files from one package form a
                    // single layer. Keep the definition's display name while
                    // pairing it with the locale value regardless of archive
                    // traversal order.
                    let current = &mut result[index];
                    if entry.def_locale_key_present {
                        if !entry.source_name.trim().is_empty() {
                            current.source_name = entry.source_name.clone();
                        }
                        current.source_path = entry.source_path.clone();
                        current.category = entry.category.clone();
                        current.unit_name = entry.unit_name.clone();
                        current.def_locale_key_present = true;
                        if !entry.value.is_empty() || !current.locale_key_present {
                            current.value = entry.value.clone();
                            current.status = entry.status.clone();
                        }
                    }
                    if entry.locale_key_present {
                        current.value = entry.value.clone();
                        current.status = entry.status.clone();
                        current.locale_key_present = true;
                        current.locale_key = entry.locale_key.clone();
                        if current.source_name.trim().is_empty()
                            && !entry.source_name.trim().is_empty()
                        {
                            current.source_name = entry.source_name.clone();
                        }
                    }
                    continue;
                }
                if entry.locale_key_present && !entry.def_locale_key_present {
                    // A locale-only entry from a higher-priority package only
                    // supplies the translated value; keep the definition's
                    // source name, package and path so the originating map
                    // mod remains traceable.
                    let current = &mut result[index];
                    current.value = entry.value.clone();
                    current.status = entry.status.clone();
                    current.locale_key_present = true;
                    current.locale_key = entry.locale_key.clone();
                } else {
                    // A higher-priority definition replaces the whole entry.
                    // A high-priority definition must not inherit a
                    // translation from a lower-priority mod.
                    result[index] = entry;
                }
            } else {
                positions.insert(merge_key, result.len());
                result.push(entry);
            }
        }
    }
    result
}

fn local_packages_for_profile(
    paths: &Paths,
    connection: &mut Connection,
    profile: &ProfileDto,
    cancelled: &AtomicBool,
) -> Result<Vec<ModDto>, String> {
    let mut mods = dedupe_mods(load_cached(connection)?);
    // Reconcile the shallow package catalog on every request so newly added
    // and removed packages are visible without re-reading game assets.
    let mut discovered = discover_packages(Some(&paths.mod_root), false, cancelled);
    discovered.extend(discover_workshop_packages(&paths.workshop_roots, cancelled));
    if !discovered.is_empty() || !mods.is_empty() {
        sync_index(connection, &discovered)?;
        mods = dedupe_mods(load_cached(connection)?);
    }
    let active = active_for_profile(profile)?;
    apply_enabled(&mut mods, &active);
    Ok(order_localization_packages(mods, &active))
}

fn order_localization_packages(mods: Vec<ModDto>, active: &[String]) -> Vec<ModDto> {
    let mut remaining: Vec<ModDto> = mods
        .into_iter()
        .filter(|row| row.enabled && !row.path.is_empty())
        .collect();
    let mut ordered = Vec::with_capacity(remaining.len());
    for package in profile_order_to_ui(active) {
        if let Some(index) = remaining
            .iter()
            .position(|row| row_matches_active(row, &package))
        {
            ordered.push(remaining.remove(index));
        }
    }
    remaining.sort_by(|left, right| {
        left.display_name
            .to_lowercase()
            .cmp(&right.display_name.to_lowercase())
            .then_with(|| left.path.to_lowercase().cmp(&right.path.to_lowercase()))
    });
    ordered.extend(remaining);
    ordered
}

fn localization_scan_inputs(
    state: &State<'_, BackendState>,
) -> Result<(Paths, PathBuf, Arc<AtomicBool>), String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    Ok((
        backend.paths.clone(),
        backend.database_path.clone(),
        Arc::clone(&backend.localization_cancelled),
    ))
}

fn localization_scan_impl(
    request: LocalizationScanRequest,
    paths: Paths,
    database_path: PathBuf,
    cancelled: Arc<AtomicBool>,
    #[cfg(feature = "desktop")] app: Option<AppHandle>,
) -> Result<LocalizationScanDto, String> {
    let _directory_lock = mod_directory::read_lock(&paths.mod_root)?;
    let started = std::time::Instant::now();
    let locale = request
        .target_locale
        .unwrap_or_else(|| "zh_cn".into())
        .trim()
        .to_ascii_lowercase();
    let locale_bytes = locale.as_bytes();
    if locale_bytes.len() != 5
        || locale_bytes[2] != b'_'
        || !locale_bytes[..2]
            .iter()
            .all(|value| value.is_ascii_lowercase())
        || !locale_bytes[3..]
            .iter()
            .all(|value| value.is_ascii_lowercase())
    {
        return Err("Target locale must use the xx_yy format.".into());
    }
    let profile = find_profile(&paths, &request.profile_id).ok_or("Profile not found.")?;
    app_log(
        "INFO",
        &format!(
            "localization scan start: locale={} profile={}",
            locale, request.profile_id
        ),
    );
    let mut connection = open_db(&database_path)?;
    let base_file = request
        .base_file
        .or_else(|| read_localization_base(&connection).ok().flatten());
    // Lowest to highest priority: game base/DLC, enabled mods in profile order,
    // then the user base file. Later layers replace earlier values.
    let mut packages = system_localization_packages(&paths);
    let mut enabled_mods =
        local_packages_for_profile(&paths, &mut connection, &profile, &cancelled)?;
    // Profile order is low -> high priority, but order_localization_packages
    // returns high -> low (UI order). Reverse it back so the merge below lets a
    // higher-priority mod override a lower-priority one.
    enabled_mods.reverse();
    packages.append(&mut enabled_mods);
    if let Some(path) = base_file.filter(|value| Path::new(value).is_file()) {
        packages.push(ModDto {
            id: "user-localization-base".into(),
            package_name: "User localization base".into(),
            path,
            package_type: "localization-base".into(),
            display_name: "User localization base".into(),
            author: String::new(),
            version: String::new(),
            size: 0,
            modified_ms: 0,
            enabled: true,
            category: "base".into(),
            fingerprint: 0,
        });
    }
    let mut all_entries = Vec::new();
    let mut inspected = 0usize;
    let mut cached = 0usize;
    for package in &packages {
        if cancelled.load(Ordering::Relaxed) {
            return Err("Localization scan cancelled.".into());
        }
        let fingerprint = package_fingerprint(Path::new(&package.path));
        #[cfg(feature = "desktop")]
        if let Some(app) = &app {
            let _ = app.emit(
                "localization-progress",
                serde_json::json!({
                    "packageName": package.display_name,
                    "path": package.path,
                    "processed": inspected + cached,
                    "total": packages.len()
                }),
            );
        }
        if let Some(entries) =
            load_localization_snapshot(&connection, &package.path, &locale, &fingerprint)?
        {
            cached += 1;
            app_log("INFO", &format!("localization cache hit: {}", package.path));
            all_entries.push(
                entries
                    .into_iter()
                    .filter(|entry| {
                        is_localization_source_path_for_locale(&entry.source_path, &locale)
                    })
                    .collect(),
            );
            continue;
        }
        inspected += 1;
        let entries = scan_localization_package(
            Path::new(&package.path),
            &locale,
            &cancelled,
            #[cfg(feature = "desktop")]
            app.as_ref(),
        )
        .into_iter()
        .filter(|entry| is_localization_source_path_for_locale(&entry.source_path, &locale))
        .collect::<Vec<_>>();
        if cancelled.load(Ordering::Relaxed) {
            return Err("Localization scan cancelled.".into());
        }
        // Persist each completed package immediately. This keeps progress
        // durable even if a later archive is slow, cancelled, or fails.
        let snapshot = (package.path.clone(), fingerprint, entries.clone());
        save_localization_snapshot(&mut connection, &locale, &snapshot)?;
        app_log(
            "INFO",
            &format!(
                "localization scan: {} -> {} entries",
                package.path,
                entries.len()
            ),
        );
        all_entries.push(entries);
    }
    if cancelled.load(Ordering::Relaxed) {
        return Err("Localization scan cancelled.".into());
    }
    let current_paths = packages
        .iter()
        .map(|package| package.path.clone())
        .collect::<Vec<_>>();
    // A final empty write performs stale-package cleanup without rewriting
    // snapshots that were already persisted incrementally above.
    save_localization_snapshots(&mut connection, &locale, &current_paths, &[])?;
    assign_missing_locale_keys(&mut all_entries);
    let entries = merge_localization_entries(all_entries);
    app_log(
        "INFO",
        &format!(
            "localization scan done: packages={} inspected={} cached={} entries={} elapsed_ms={}",
            packages.len(),
            inspected,
            cached,
            entries.len(),
            started.elapsed().as_millis()
        ),
    );
    // The per-package progress events stop at total - 1, so emit one final
    // event at processed == total to leave the UI at a completed state.
    #[cfg(feature = "desktop")]
    if let Some(app) = &app {
        let _ = app.emit(
            "localization-progress",
            serde_json::json!({
                "packageName": "",
                "path": "",
                "processed": packages.len(),
                "total": packages.len()
            }),
        );
    }
    Ok(LocalizationScanDto {
        packages: packages.len(),
        inspected,
        cached,
        elapsed_ms: started.elapsed().as_millis(),
        entries,
    })
}

fn system_localization_packages(paths: &Paths) -> Vec<ModDto> {
    let Some(game_executable) = &paths.game_executable else {
        return Vec::new();
    };
    let Some(game_root) = game_executable
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
    else {
        return Vec::new();
    };
    let mut files: Vec<PathBuf> = fs::read_dir(game_root)
        .ok()
        .into_iter()
        .flat_map(|entries| entries.flatten().map(|entry| entry.path()))
        .filter(|path| {
            let name = path
                .file_name()
                .and_then(|value| value.to_str())
                .unwrap_or_default()
                .to_ascii_lowercase();
            path.is_file()
                && path
                    .extension()
                    .and_then(|value| value.to_str())
                    .is_some_and(|ext| ext.eq_ignore_ascii_case("scs"))
                && (name == "base.scs"
                    || name == "def.scs"
                    || name == "locale.scs"
                    || name.starts_with("dlc_"))
        })
        .collect();
    files.sort_by_key(|path| {
        let name = path
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or_default()
            .to_ascii_lowercase();
        // Lowest layer first: base.scs, then def.scs (definitions), then
        // locale.scs (translated values), then DLCs. locale.scs must come
        // after def.scs so its locale-only entries fill the definitions'
        // @@key@@ references during the merge below.
        let order = if name == "base.scs" {
            0
        } else if name == "def.scs" {
            1
        } else if name == "locale.scs" {
            2
        } else {
            3
        };
        (order, name)
    });
    files
        .into_iter()
        .map(|path| {
            let name = path
                .file_name()
                .and_then(|value| value.to_str())
                .unwrap_or_default()
                .to_string();
            let metadata = fs::metadata(&path).ok();
            ModDto {
                id: name.clone(),
                package_name: name.clone(),
                path: normalize_path(&path),
                package_type: "system".into(),
                display_name: name,
                author: String::new(),
                version: String::new(),
                size: metadata
                    .as_ref()
                    .map(|value| value.len())
                    .unwrap_or_default(),
                modified_ms: metadata
                    .and_then(|value| value.modified().ok())
                    .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
                    .map(|value| value.as_millis() as i64)
                    .unwrap_or_default(),
                enabled: true,
                category: "system".into(),
                fingerprint: 0,
            }
        })
        .collect()
}

fn read_localization_base(connection: &Connection) -> Result<Option<String>, String> {
    connection
        .query_row(
            "SELECT value FROM app_setting WHERE key = 'localization.base_file'",
            [],
            |row| row.get(0),
        )
        .optional()
        .map_err(|error| format!("read localization base failed: {error}"))
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn localization_base_get(state: State<'_, BackendState>) -> Result<Option<String>, String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    let connection = open_db(&backend.database_path)?;
    read_localization_base(&connection)
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn localization_base_pick(state: State<'_, BackendState>) -> Result<Option<String>, String> {
    #[cfg(windows)]
    {
        let default_directory = {
            let backend = state
                .inner
                .lock()
                .map_err(|_| "backend lock poisoned".to_string())?;
            backend.paths.mod_root.clone()
        };
        let Some(path) = rfd::FileDialog::new()
            .set_directory(&default_directory)
            .add_filter("Localization files", &["scs", "zip", "sii", "sui"])
            .pick_file()
        else {
            return Ok(None);
        };
        let value = normalize_path(&path);
        let backend = state
            .inner
            .lock()
            .map_err(|_| "backend lock poisoned".to_string())?;
        let connection = open_db(&backend.database_path)?;
        let _lock = acquire_db_write_lock();
        connection
            .execute(
                "INSERT INTO app_setting(key, value) VALUES ('localization.base_file', ?1)
             ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                params![value],
            )
            .map_err(|error| format!("save localization base failed: {error}"))?;
        return Ok(Some(value));
    }
    #[cfg(not(windows))]
    {
        let _ = state;
        Err("Selecting a localization base file is only supported on Windows.".into())
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct LocalizationSaveAsRequest {
    entries: Vec<LocalizationEntryDto>,
    suggested_name: String,
}

// Renders entries as a standalone SiiNunit localization_db file that the
// game (or a later scan) can load directly.
fn localization_export_text(entries: &[LocalizationEntryDto]) -> String {
    let mut output = String::from("SiiNunit\n{\n\nlocalization_db : .localization\n{\n");
    for entry in entries {
        let key = entry.locale_key.trim();
        if key.is_empty() || key.starts_with("__manual_") {
            continue;
        }
        output.push_str(&format!("key[]: \"{}\"\n", key));
        output.push_str(&format!("val[]: \"{}\"\n", escape_sii(&entry.value)));
        output.push('\n');
    }
    output.push_str("}\n}\n");
    output
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn localization_save_as(
    state: State<'_, BackendState>,
    request: LocalizationSaveAsRequest,
) -> Result<Option<String>, String> {
    let (game_root, _mod_root) = {
        let backend = state
            .inner
            .lock()
            .map_err(|_| "backend lock poisoned".to_string())?;
        (
            backend.paths.game_root.clone(),
            backend.paths.mod_root.clone(),
        )
    };
    // Default to the user's ETS2 documents folder.
    let default_directory = game_root
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    let suggested = if request.suggested_name.trim().is_empty() {
        "localization.sui".to_string()
    } else {
        request.suggested_name.trim().to_string()
    };
    let Some(path) = rfd::FileDialog::new()
        .set_directory(&default_directory)
        .set_file_name(&suggested)
        .add_filter("Localization files", &["sui", "sii"])
        .save_file()
    else {
        return Ok(None);
    };
    let path = normalize_path(&path);
    let text = localization_export_text(&request.entries);
    fs::write(&path, text.as_bytes())
        .map_err(|e| format!("write localization file failed: {e}"))?;
    app_log("INFO", &format!("localization exported to {}", path));
    Ok(Some(path))
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn localization_write_base(request: LocalizationWriteRequest) -> Result<(), String> {
    let path = PathBuf::from(&request.base_file);
    let extension = path
        .extension()
        .and_then(|v| v.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    if extension != "sii" && extension != "sui" {
        return Err("当前仅支持将文本 .sii/.sui 基底文件直接写回；请先选择文本基底文件。".into());
    }
    let original =
        fs::read_to_string(&path).map_err(|e| format!("read localization base failed: {e}"))?;
    let mut output = original.clone();
    for entry in request.entries {
        let value = escape_sii(&entry.value);
        let pattern = Regex::new(&format!(
            r#"(?m)(\b{}\s*:\s*")((?:\\.|[^"\\])*)(")"#,
            regex::escape(&entry.locale_key)
        ))
        .map_err(|e| format!("build localization replacement failed: {e}"))?;
        if pattern.is_match(&output) {
            output = pattern
                .replace_all(&output, |caps: &regex::Captures| {
                    format!("{}{}{}", &caps[1], value, &caps[3])
                })
                .into_owned();
        } else {
            output.push_str(&format!("\n{}: \"{}\"\n", entry.locale_key, value));
        }
    }
    let temp = path.with_extension("ets2mm.tmp");
    fs::write(&temp, output.as_bytes())
        .map_err(|e| format!("write localization base failed: {e}"))?;
    fs::rename(&temp, &path).map_err(|e| {
        let _ = fs::remove_file(&temp);
        format!("replace localization base failed: {e}")
    })?;
    Ok(())
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
async fn localization_scan(
    request: LocalizationScanRequest,
    state: State<'_, BackendState>,
    #[cfg(feature = "desktop")] app: AppHandle,
) -> Result<LocalizationScanDto, String> {
    let (paths, database_path, cancelled) = localization_scan_inputs(&state)?;
    cancelled.store(false, Ordering::Relaxed);

    #[cfg(feature = "desktop")]
    {
        return tauri::async_runtime::spawn_blocking(move || {
            localization_scan_impl(request, paths, database_path, cancelled, Some(app))
        })
        .await
        .map_err(|error| format!("localization scan worker failed: {error}"))?;
    }

    #[cfg(not(feature = "desktop"))]
    localization_scan_impl(request, paths, database_path, cancelled)
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn localization_cancel(state: State<'_, BackendState>) -> Result<(), String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    backend
        .localization_cancelled
        .store(true, Ordering::Relaxed);
    Ok(())
}

fn crash_pair(paths: &Paths) -> CrashPairDto {
    let documents = paths
        .game_root
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    let candidates = [
        ("ets2", documents.join("Euro Truck Simulator 2")),
        ("ats", documents.join("American Truck Simulator")),
    ];
    let mut latest_crash = None;
    let mut latest_log = None;
    for (source, root) in candidates {
        let crash = root.join("game.crash.txt");
        if crash.is_file() {
            let modified = fs::metadata(&crash)
                .and_then(|value| value.modified())
                .ok()
                .unwrap_or(UNIX_EPOCH);
            if latest_crash
                .as_ref()
                .is_none_or(|(_, current, _)| modified > *current)
            {
                latest_crash = Some((source, modified, crash));
            }
        }
        let log = root.join("game.log.txt");
        if log.is_file() {
            let modified = fs::metadata(&log)
                .and_then(|value| value.modified())
                .ok()
                .unwrap_or(UNIX_EPOCH);
            if latest_log
                .as_ref()
                .is_none_or(|(_, current, _)| modified > *current)
            {
                latest_log = Some((source, modified, log));
            }
        }
    }
    if let Some((source, _, crash)) = latest_crash {
        let log = crash
            .parent()
            .map(|root| root.join("game.log.txt"))
            .filter(|path| path.is_file());
        return CrashPairDto {
            crash_path: Some(normalize_path(&crash)),
            log_path: log.map(|path| normalize_path(&path)),
            source: Some(source.into()),
        };
    }
    if let Some((source, _, log)) = latest_log {
        return CrashPairDto {
            crash_path: None,
            log_path: Some(normalize_path(&log)),
            source: Some(source.into()),
        };
    }
    CrashPairDto {
        crash_path: None,
        log_path: None,
        source: None,
    }
}

const CRASH_FATAL_MARKERS: [&str; 13] = [
    "fatal", "unhandled exception", "panic", "abort", "segmentation fault",
    "access violation", "stack overflow", "assertion failed", "crash",
    "exception code", "fatal error", "unexpected termination", "fault address",
];
const CRASH_ERROR_MARKERS: [&str; 10] = [
    "error", "failed", "failure", "unable", "invalid", "corrupt", "cannot",
    "could not", "missing", "not found",
];
const CRASH_WARNING_MARKERS: [&str; 8] = [
    "warning", "warn", "deprecated", "ignored", "fallback", "skipping",
    "optional", "unsupported",
];

fn read_log_tail(path: &Path, max_bytes: usize) -> Result<String, String> {
    let bytes = fs::read(path).map_err(|error| format!("read log failed: {error}"))?;
    let start = bytes.len().saturating_sub(max_bytes);
    Ok(String::from_utf8_lossy(&bytes[start..]).into_owned())
}

fn analyze_crash_logs(
    pair: &CrashPairDto,
    active: &[String],
    mods: &[ModDto],
) -> (String, Vec<String>, Vec<CrashIssueDto>) {
    let mut evidence = Vec::new();
    let mut issues = Vec::new();
    let mut matched = HashSet::new();
    let mut aliases = Vec::new();
    for package in active {
        let mut values = package_aliases(package);
        if let Some(row) = mods.iter().find(|row| row_matches_active(row, package)) {
            for value in [&row.id, &row.package_name, &row.display_name] {
                values.extend(package_aliases(value));
            }
        }
        values.retain(|value| value.len() >= 4);
        values.sort();
        values.dedup();
        aliases.push((package.clone(), values));
    }

    let mut sources = Vec::new();
    if let Some(path) = pair.crash_path.as_deref() {
        sources.push(("crash", PathBuf::from(path)));
    }
    if let Some(path) = pair.log_path.as_deref() {
        sources.push(("log", PathBuf::from(path)));
    }
    for (kind, path) in sources {
        let Ok(text) = read_log_tail(&path, 4 * 1024 * 1024) else {
            continue;
        };
        for (line_index, raw_line) in text.lines().enumerate() {
            let line = raw_line.trim();
            if line.is_empty() {
                continue;
            }
            let lower = line.to_ascii_lowercase();
            let fatal = CRASH_FATAL_MARKERS.iter().any(|marker| lower.contains(marker));
            let error = CRASH_ERROR_MARKERS.iter().any(|marker| lower.contains(marker));
            let warning = CRASH_WARNING_MARKERS.iter().any(|marker| lower.contains(marker));
            // A package name by itself is normal startup noise. Only retain
            // lines that carry an actual diagnostic signal.
            if !(fatal || error || warning) {
                continue;
            }
            let short = if line.len() > 320 {
                let mut end = 320;
                while end > 0 && !line.is_char_boundary(end) {
                    end -= 1;
                }
                &line[..end]
            } else {
                line
            };
            let item = format!("{kind} line {}: {short}", line_index + 1);
            if evidence.len() < 40 {
                evidence.push(item.clone());
            }
            for (index, (package, package_aliases)) in aliases.iter().enumerate() {
                if !package_aliases.iter().any(|alias| lower.contains(alias)) {
                    continue;
                }
                let key = format!("{package}:{kind}");
                if !matched.insert(key) {
                    continue;
                }
                let row = mods.iter().find(|row| row_matches_active(row, package));
                let is_crash_evidence = fatal || (kind == "crash" && error && !warning);
                let severity = if is_crash_evidence {
                    "red"
                } else {
                    "yellow"
                };
                let code = if is_crash_evidence {
                    if kind == "crash" {
                        "CRASH_MOD_REFERENCE"
                    } else {
                        "FATAL_LOG_MOD_REFERENCE"
                    }
                } else if warning {
                    "LOG_WARNING_REFERENCE"
                } else {
                    "LOG_ERROR_REFERENCE"
                };
                issues.push(CrashIssueDto {
                    mod_id: package.clone(),
                    display_name: row
                        .map(|row| row.display_name.clone())
                        .unwrap_or_else(|| package.clone()),
                    severity: severity.into(),
                    code: code.into(),
                    evidence: item.clone(),
                    priority_index: Some(index),
                });
            }
        }
    }
    let summary = if pair.crash_path.is_some() || pair.log_path.is_some() {
        if issues.is_empty() {
            "日志已读取，但没有发现能直接关联到当前启用 Mod 的错误证据。".into()
        } else {
            format!("日志中发现 {} 个可能相关的启用 Mod。", issues.len())
        }
    } else {
        "未找到 game.crash.txt 或 game.log.txt。".into()
    };
    (summary, evidence, issues)
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn crash_discover(state: State<'_, BackendState>) -> Result<CrashPairDto, String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    Ok(crash_pair(&backend.paths))
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct UpdateInfoDto {
    has_update: bool,
    latest_version: String,
    current_version: String,
    release_name: String,
    release_notes: String,
    asset_name: String,
    asset_size: u64,
    download_url: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct UpdateDownloadRequest {
    url: String,
    filename: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct UpdateDownloadDto {
    path: String,
}

const UPDATE_LATEST_URL: &str =
    "https://api.github.com/repos/HimenoKoutarou/ets2-mod-manager/releases/latest";

fn update_http_client() -> Result<reqwest::blocking::Client, String> {
    reqwest::blocking::Client::builder()
        .user_agent("ETS2ModManager/update")
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|error| format!("build update client failed: {error}"))
}

fn version_parts(version: &str) -> Vec<u64> {
    version
        .trim()
        .trim_start_matches('v')
        .split('.')
        .filter_map(|part| {
            part.chars()
                .take_while(|c| c.is_ascii_digit())
                .collect::<String>()
                .parse::<u64>()
                .ok()
        })
        .collect()
}

fn version_is_newer(latest: &str, current: &str) -> bool {
    let latest = version_parts(latest);
    let current = version_parts(current);
    for (index, part) in latest.iter().enumerate() {
        let current_part = current.get(index).copied().unwrap_or(0);
        if *part > current_part {
            return true;
        }
        if *part < current_part {
            return false;
        }
    }
    latest.len() > current.len()
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn check_update() -> Result<UpdateInfoDto, String> {
    let current_version = env!("CARGO_PKG_VERSION");
    let client = update_http_client()?;
    let response = client
        .get(UPDATE_LATEST_URL)
        .send()
        .map_err(|error| format!("check update failed: {error}"))?;
    if !response.status().is_success() {
        return Err(format!("check update failed: HTTP {}", response.status()));
    }
    let payload: serde_json::Value = serde_json::from_str(
        &response
            .text()
            .map_err(|error| format!("read release failed: {error}"))?,
    )
    .map_err(|error| format!("parse release failed: {error}"))?;
    let tag = payload
        .get("tag_name")
        .and_then(|value| value.as_str())
        .unwrap_or_default();
    let latest_version = tag.trim_start_matches('v').to_string();
    let release_name = payload
        .get("name")
        .and_then(|value| value.as_str())
        .unwrap_or_default()
        .to_string();
    let release_notes = payload
        .get("body")
        .and_then(|value| value.as_str())
        .unwrap_or_default()
        .to_string();
    let assets = payload.get("assets").and_then(|value| value.as_array());
    // Prefer the NSIS installer (.exe), then .msi, then the first asset.
    let chosen = assets.and_then(|list| {
        list.iter()
            .find(|asset| {
                asset
                    .get("name")
                    .and_then(|v| v.as_str())
                    .is_some_and(|n| n.ends_with(".exe"))
            })
            .or_else(|| {
                list.iter().find(|asset| {
                    asset
                        .get("name")
                        .and_then(|v| v.as_str())
                        .is_some_and(|n| n.ends_with(".msi"))
                })
            })
            .or_else(|| list.first())
    });
    let (asset_name, asset_size, download_url) = match chosen {
        Some(asset) => (
            asset
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_string(),
            asset
                .get("size")
                .and_then(|v| v.as_u64())
                .unwrap_or_default(),
            asset
                .get("browser_download_url")
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_string(),
        ),
        None => (String::new(), 0, String::new()),
    };
    Ok(UpdateInfoDto {
        has_update: version_is_newer(&latest_version, current_version),
        latest_version,
        current_version: current_version.to_string(),
        release_name,
        release_notes,
        asset_name,
        asset_size,
        download_url,
    })
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn download_update(
    #[cfg(feature = "desktop")] app: AppHandle,
    request: UpdateDownloadRequest,
) -> Result<UpdateDownloadDto, String> {
    let url = request.url.trim();
    if url.is_empty() || !(url.starts_with("https://") || url.starts_with("http://")) {
        return Err("invalid download url".into());
    }
    // Take only the final path segment to guard against path traversal.
    let filename = request
        .filename
        .trim()
        .split(['/', '\\'])
        .last()
        .unwrap_or_default()
        .to_string();
    let filename = if filename.is_empty() {
        "ets2-mod-manager-update.exe".to_string()
    } else {
        filename
    };
    let client = update_http_client()?;
    let response = client
        .get(url)
        .send()
        .map_err(|error| format!("download failed: {error}"))?;
    if !response.status().is_success() {
        return Err(format!("download failed: HTTP {}", response.status()));
    }
    let total = response.content_length().unwrap_or(0);
    let update_dir = std::env::temp_dir().join("ets2modmanager-update");
    fs::create_dir_all(&update_dir)
        .map_err(|error| format!("create update directory failed: {error}"))?;
    let destination = update_dir.join(&filename);
    let mut reader = response;
    let mut file = fs::File::create(&destination)
        .map_err(|error| format!("create download file failed: {error}"))?;
    let mut buffer = [0u8; 64 * 1024];
    let mut downloaded = 0u64;
    loop {
        let read = reader
            .read(&mut buffer)
            .map_err(|error| format!("read download failed: {error}"))?;
        if read == 0 {
            break;
        }
        file.write_all(&buffer[..read])
            .map_err(|error| format!("write download failed: {error}"))?;
        downloaded = downloaded.saturating_add(read as u64);
        #[cfg(feature = "desktop")]
        let _ = app.emit(
            "update-download-progress",
            serde_json::json!({ "downloaded": downloaded, "total": total }),
        );
    }
    drop(file);
    if downloaded == 0 {
        let _ = fs::remove_file(&destination);
        return Err("download failed: empty response".into());
    }
    app_log(
        "INFO",
        &format!(
            "update downloaded: {} bytes -> {}",
            downloaded,
            destination.display()
        ),
    );
    Ok(UpdateDownloadDto {
        path: destination.to_string_lossy().to_string(),
    })
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct InstallUpdateRequest {
    path: String,
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn install_update(request: InstallUpdateRequest) -> Result<(), String> {
    let path = PathBuf::from(request.path.trim());
    if !path.is_file() {
        return Err(format!("Installer was not found: {}", path.display()));
    }
    let Some(name) = path.file_name().and_then(|value| value.to_str()) else {
        return Err("Installer path is invalid.".into());
    };
    if !name.to_ascii_lowercase().ends_with(".exe") {
        return Err("Installer must be an .exe file.".into());
    }
    app_log(
        "INFO",
        &format!("launching silent installer: {}", path.display()),
    );
    let mut command = std::process::Command::new(&path);
    hide_child_process(&mut command);
    // /S: silent install; the Tauri NSIS installer auto-kills the running
    // instance before overwriting files. /R: (re)start the app after the
    // install finishes. The freshly started process cleans up the leftover
    // installer from %TEMP%\ets2modmanager-update on startup.
    command.args(["/S", "/R"]);
    let Ok(_child) = command.spawn() else {
        return Err(format!("failed to start installer: {}", path.display()));
    };
    Ok(())
}

// Removes leftover update installers from a previous silent install.
fn cleanup_update_downloads() {
    let update_dir = std::env::temp_dir().join("ets2modmanager-update");
    if let Ok(entries) = fs::read_dir(&update_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_file() {
                let _ = fs::remove_file(&path);
            }
        }
    }
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn save_inspect_bsii(request: BsiiInspectRequest) -> Result<BsiiSummaryDto, String> {
    let path = PathBuf::from(request.path);
    if !path.is_file() {
        return Err(format!("Save file was not found: {}", path.display()));
    }
    let bytes = fs::read(&path).map_err(|error| format!("read BSII file failed: {error}"))?;
    let bytes = decode_scsc_or_plain(&bytes)?;
    let summary =
        bsii_core::parse_summary(&bytes).map_err(|error| format!("parse BSII failed: {error}"))?;
    Ok(BsiiSummaryDto {
        version: summary.version,
        definitions: summary.definitions,
        objects: summary.objects,
    })
}

fn save_snapshot(path: &Path) -> Result<SaveSnapshotDto, String> {
    let bytes = fs::read(path).map_err(|error| format!("read save failed: {error}"))?;
    let bytes = decode_scsc_or_plain(&bytes)?;
    let header = bsii_core::inspect_header(&bytes).map_err(str::to_string)?;
    let fields = bsii_core::find_numeric_fields(&bytes, &["money_account", "experience_points"])
        .map_err(|error| format!("parse save failed: {error}"))?
        .into_iter()
        .map(|field| SaveFieldDto {
            structure_name: field.structure_name,
            field_name: field.field_name,
            type_id: field.type_id,
            value: field.value,
            offset: field.offset,
            size: field.size,
        })
        .collect();
    Ok(SaveSnapshotDto {
        version: header.version,
        fields,
    })
}

fn level_xp(level: i64) -> Result<i64, String> {
    if !(1..=200).contains(&level) {
        return Err("Level must be between 1 and 200.".into());
    }
    level
        .checked_mul(level - 1)
        .and_then(|value| value.checked_mul(500))
        .ok_or_else(|| "Level value is out of range.".into())
}

fn mutate_save(path: &Path, operation: &str, value: i64) -> Result<SaveMutationDto, String> {
    if is_game_running() {
        return Err("Exit ETS2 or ATS before editing a save.".into());
    }
    let original = fs::read(path).map_err(|error| format!("read save failed: {error}"))?;
    let plain = decode_scsc_or_plain(&original)?;
    let target_field = match operation {
        "set_money" => ("bank", "money_account", 0x31u32, 8usize),
        "set_experience" => ("economy", "experience_points", 0x27u32, 4usize),
        "set_level" => ("economy", "experience_points", 0x27u32, 4usize),
        _ => return Err("Unsupported save operation.".into()),
    };
    let write_value = if operation == "set_level" {
        level_xp(value)?
    } else {
        value
    };
    if operation == "set_experience" && !(0..=(u32::MAX as i64)).contains(&write_value) {
        return Err("Experience must be between 0 and 4294967295.".into());
    }
    if operation == "set_money" && !(i64::MIN..=i64::MAX).contains(&write_value) {
        return Err("Money value is out of range.".into());
    }
    let fields = bsii_core::find_numeric_fields(&plain, &[target_field.1])
        .map_err(|error| format!("parse save failed: {error}"))?;
    let matches: Vec<_> = fields
        .into_iter()
        .filter(|field| {
            field.structure_name == target_field.0
                && field.field_name == target_field.1
                && field.type_id == target_field.2
                && field.size == target_field.3
        })
        .collect();
    if matches.len() != 1 {
        return Err(format!(
            "Field {} was not found uniquely in the BSII save.",
            target_field.1
        ));
    }
    let field = &matches[0];
    if field.value == write_value {
        return Ok(SaveMutationDto {
            success: false,
            operation: operation.into(),
            message: "The requested value is already stored.".into(),
            backup_path: None,
            value: Some(field.value),
        });
    }
    let mut output_plain = plain.clone();
    match field.type_id {
        0x27 => output_plain[field.offset..field.offset + 4]
            .copy_from_slice(&(write_value as u32).to_le_bytes()),
        0x31 => {
            output_plain[field.offset..field.offset + 8].copy_from_slice(&write_value.to_le_bytes())
        }
        _ => unreachable!(),
    }
    let output = if original.starts_with(b"ScsC") {
        encode_scsc(&output_plain)?
    } else {
        output_plain
    };
    let backup = transaction_backup(path, "save")?;
    if let Err(error) = atomic_write(path, &output) {
        return Err(transaction_failure(path, &backup, error));
    }
    let verify = match save_snapshot(path) {
        Ok(snapshot) => snapshot,
        Err(verify_error) => {
            return Err(transaction_failure(
                path,
                &backup,
                format!("Save write verification read failed: {verify_error}"),
            ));
        }
    };
    let verified = verify
        .fields
        .iter()
        .find(|entry| {
            entry.structure_name == target_field.0
                && entry.field_name == target_field.1
                && entry.type_id == target_field.2
                && entry.size == target_field.3
        })
        .map(|entry| entry.value);
    if verified != Some(write_value) {
        return Err(transaction_failure(
            path,
            &backup,
            "Save write verification failed",
        ));
    }
    Ok(SaveMutationDto {
        success: true,
        operation: operation.into(),
        message: format!("Updated {}.", target_field.1),
        backup_path: Some(normalize_path(&backup)),
        value: Some(write_value),
    })
}

fn mutate_object_numeric(request: SaveObjectMutationRequest) -> Result<SaveMutationDto, String> {
    if is_game_running() {
        return Err("Exit ETS2 or ATS before editing a save.".into());
    }
    let path = Path::new(&request.path);
    let original = fs::read(path).map_err(|error| format!("read save failed: {error}"))?;
    let plain = decode_scsc_or_plain(&original)?;
    let fields = bsii_core::find_numeric_fields(&plain, &[request.field_name.as_str()])
        .map_err(|error| format!("parse save failed: {error}"))?;
    let matches: Vec<_> = fields
        .into_iter()
        .filter(|field| {
            field.object_index == request.object_index
                && field.structure_name == request.structure_name
                && field.field_name == request.field_name
        })
        .collect();
    if matches.len() != 1 {
        return Err(format!(
            "Field {} was not found uniquely in object {}.",
            request.field_name, request.object_index
        ));
    }
    let field = &matches[0];
    let valid = match field.type_id {
        0x27 | 0x2F => (0..=u32::MAX as i64).contains(&request.value),
        0x31 => true,
        0x35 => (0..=u8::MAX as i64).contains(&request.value),
        _ => false,
    };
    if !valid {
        return Err(format!("Unsupported or out-of-range numeric field {}.", request.field_name));
    }
    if field.value == request.value {
        return Ok(SaveMutationDto {
            success: false,
            operation: "set_object_field".into(),
            message: "The requested value is already stored.".into(),
            backup_path: None,
            value: Some(field.value),
        });
    }
    let mut output_plain = plain.clone();
    match field.type_id {
        0x27 | 0x2F => output_plain[field.offset..field.offset + 4]
            .copy_from_slice(&(request.value as u32).to_le_bytes()),
        0x31 => output_plain[field.offset..field.offset + 8]
            .copy_from_slice(&request.value.to_le_bytes()),
        0x35 => output_plain[field.offset] = request.value as u8,
        _ => return Err("Unsupported numeric field type.".into()),
    }
    let output = if original.starts_with(b"ScsC") {
        encode_scsc(&output_plain)?
    } else {
        output_plain
    };
    let backup = transaction_backup(path, "save")?;
    if let Err(error) = atomic_write(path, &output) {
        return Err(transaction_failure(path, &backup, error));
    }
    let verify_bytes = decode_scsc_or_plain(&fs::read(path).map_err(|error| format!("verify save read failed: {error}"))?)?;
    let verified = bsii_core::find_numeric_fields(&verify_bytes, &[request.field_name.as_str()])
        .map_err(|error| format!("verify save parse failed: {error}"))?
        .into_iter()
        .find(|entry| {
            entry.object_index == request.object_index
                && entry.structure_name == request.structure_name
                && entry.field_name == request.field_name
        })
        .map(|entry| entry.value);
    if verified != Some(request.value) {
        return Err(transaction_failure(path, &backup, "Save object write verification failed"));
    }
    Ok(SaveMutationDto {
        success: true,
        operation: "set_object_field".into(),
        message: format!("Updated {}.", request.field_name),
        backup_path: Some(normalize_path(&backup)),
        value: Some(request.value),
    })
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn save_read_snapshot(request: BsiiInspectRequest) -> Result<SaveSnapshotDto, String> {
    save_snapshot(Path::new(&request.path))
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn save_read_inventory(request: BsiiInspectRequest) -> Result<SaveInventoryDto, String> {
    let path = PathBuf::from(request.path);
    if !path.is_file() {
        return Err(format!("Save file was not found: {}", path.display()));
    }
    let bytes = fs::read(&path).map_err(|error| format!("read save failed: {error}"))?;
    let bytes = decode_scsc_or_plain(&bytes)?;
    let header = bsii_core::inspect_header(&bytes).map_err(str::to_string)?;
    let objects = bsii_core::inspect_objects(&bytes, 20_000)
        .map_err(|error| format!("inspect save objects failed: {error}"))?;
    let objects = objects
        .into_iter()
        .filter_map(|object| {
            let structure = object.structure_name.to_ascii_lowercase();
            let kind = if structure.contains("truck") {
                "truck"
            } else if structure.contains("trailer") {
                "trailer"
            } else if structure.contains("garage") {
                "garage"
            } else if structure.contains("city") || structure.contains("country") {
                "city"
            } else if structure.contains("dealer") || structure.contains("company") {
                "dealer"
            } else if structure.contains("skill") || structure.contains("upgrade") {
                "skill"
            } else if structure.contains("economy")
                || structure.contains("player")
                || structure.contains("profile")
            {
                "profile"
            } else {
                return None;
            };
            Some(SaveObjectDto {
                object_index: object.object_index,
                structure_name: object.structure_name,
                kind: kind.into(),
                fields: object
                    .fields
                    .into_iter()
                    .map(|field| SaveObjectFieldDto {
                        name: field.name,
                        type_id: field.type_id,
                        value: field.value,
                        offset: field.offset,
                        size: field.size,
                    })
                    .collect(),
            })
        })
        .collect::<Vec<_>>();
    let trucks = objects.iter().filter(|object| object.kind == "truck").count();
    let trailers = objects
        .iter()
        .filter(|object| object.kind == "trailer")
        .count();
    let garages = objects.iter().filter(|object| object.kind == "garage").count();
    let cities = objects.iter().filter(|object| object.kind == "city").count();
    let dealers = objects.iter().filter(|object| object.kind == "dealer").count();
    let skills = objects.iter().filter(|object| object.kind == "skill").count();
    Ok(SaveInventoryDto {
        version: header.version,
        objects,
        trucks,
        trailers,
        garages,
        cities,
        dealers,
        skills,
    })
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn save_mutate(request: SaveMutationRequest) -> Result<SaveMutationDto, String> {
    mutate_save(Path::new(&request.path), &request.operation, request.value)
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn save_mutate_object(request: SaveObjectMutationRequest) -> Result<SaveMutationDto, String> {
    mutate_object_numeric(request)
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn crash_precheck(
    request: CrashPrecheckRequest,
    state: State<'_, BackendState>,
) -> Result<CrashPrecheckDto, String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    let profile = find_profile(&backend.paths, &request.profile_id).ok_or("Profile not found.")?;
    let active = active_for_profile(&profile)?;
    let db = open_db(&backend.database_path)?;
    let mods = dedupe_mods(load_cached(&db)?);
    let mut aliases = HashMap::new();
    for row in &mods {
        for value in [&row.id, &row.package_name, &row.display_name] {
            for alias in package_aliases(value) {
                aliases.entry(alias).or_insert(row);
            }
        }
    }
    let mut issues = Vec::new();
    let mut seen = HashSet::new();
    for (index, package) in active.iter().rev().enumerate() {
        let canonical = canonical_package(package);
        let matched = package_aliases(package)
            .into_iter()
            .find_map(|alias| aliases.get(&alias).copied());
        if !canonical.is_empty() && !seen.insert(canonical) {
            issues.push(CrashIssueDto {
                mod_id: package.clone(),
                display_name: matched
                    .map(|row| row.display_name.clone())
                    .unwrap_or_else(|| package.clone()),
                severity: "yellow".into(),
                code: "DUPLICATE_ACTIVE_MOD".into(),
                evidence: "The profile lists this mod more than once.".into(),
                priority_index: Some(index),
            });
        } else if matched.is_none() {
            issues.push(CrashIssueDto {
                mod_id: package.clone(),
                display_name: package.clone(),
                severity: "red".into(),
                code: "MISSING_PACKAGE".into(),
                evidence: "The active profile entry was not found in the scanned package catalog."
                    .into(),
                priority_index: Some(index),
            });
        }
    }
    let pair = crash_pair(&backend.paths);
    let (log_summary, log_evidence, log_issues) = analyze_crash_logs(&pair, &active, &mods);
    for issue in log_issues {
        if !issues.iter().any(|existing: &CrashIssueDto| {
            existing.mod_id == issue.mod_id && existing.code == issue.code
        }) {
            issues.push(issue);
        }
    }
    Ok(CrashPrecheckDto {
        profile_id: profile.id,
        scanned_mods: active.len(),
        red_count: issues
            .iter()
            .filter(|issue| issue.severity == "red")
            .count(),
        yellow_count: issues
            .iter()
            .filter(|issue| issue.severity == "yellow")
            .count(),
        issues,
        log_path: pair.log_path,
        crash_path: pair.crash_path,
        log_summary,
        log_evidence,
    })
}

fn active_for_profile(profile: &ProfileDto) -> Result<Vec<String>, String> {
    let text = read_sii(&profile_sii(Path::new(&profile.folder)))?;
    Ok(parse_active_mods(&text))
}

fn profile_order_to_ui(active_mods: &[String]) -> Vec<String> {
    active_mods.iter().rev().cloned().collect()
}

fn canonical_package(value: &str) -> String {
    let mut value = value
        .split('|')
        .next()
        .unwrap_or(value)
        .trim()
        .to_ascii_lowercase();
    for suffix in ["_workshop", "_local"] {
        if let Some(prefix) = value.strip_suffix(suffix) {
            value = prefix.to_string();
        }
    }
    if let Some(index) = value.rfind("_copy") {
        let tail = &value[index + "_copy".len()..];
        if tail.is_empty() || tail.chars().all(|ch| ch.is_ascii_digit()) {
            value.truncate(index);
        }
    }
    value
}

fn package_aliases(value: &str) -> Vec<String> {
    let raw = value.trim().to_ascii_lowercase();
    if raw.is_empty() {
        return Vec::new();
    }
    let mut aliases = HashSet::new();
    aliases.insert(raw.clone());
    let package = canonical_package(&raw);
    if !package.is_empty() {
        aliases.insert(package.clone());
    }
    if let Some(separator) = raw.find('|') {
        if let Some(alias) = raw.get(separator + 1..) {
            if !alias.trim().is_empty() {
                aliases.insert(alias.trim().to_string());
            }
        }
    }
    if let Some(hex) = package.strip_prefix("mod_workshop_package.") {
        if !hex.is_empty() && hex.chars().all(|ch| ch.is_ascii_hexdigit()) {
            if let Ok(id) = u64::from_str_radix(hex, 16) {
                aliases.insert(id.to_string());
            }
        }
    }
    aliases.into_iter().collect()
}

fn rows_match(left: &str, right: &str) -> bool {
    let right_aliases: HashSet<String> = package_aliases(right).into_iter().collect();
    package_aliases(left)
        .into_iter()
        .any(|alias| right_aliases.contains(&alias))
}

fn row_matches_active(row: &ModDto, active: &str) -> bool {
    [row.id.as_str(), row.package_name.as_str(), row.display_name.as_str()]
        .into_iter()
        .any(|value| rows_match(value, active))
}

fn apply_enabled(mods: &mut [ModDto], active: &[String]) {
    for row in mods {
        row.enabled = active
            .iter()
            .any(|entry| row_matches_active(row, entry));
    }
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let temp = path.with_extension(format!("tmp-{}", now_ms()));
    fs::write(&temp, bytes).map_err(|e| format!("write temporary file failed: {e}"))?;
    if let Err(first_error) = fs::rename(&temp, path) {
        #[cfg(windows)]
        {
            let rollback = path.with_extension(format!("rollback-{}", now_ms()));
            let had_original = path.is_file();
            if had_original {
                if let Err(copy_error) = fs::copy(path, &rollback) {
                    let _ = fs::remove_file(&temp);
                    return Err(format!("replace file failed: {first_error}; create rollback copy failed: {copy_error}"));
                }
            }
            if let Err(remove_error) = fs::remove_file(path) {
                let _ = fs::remove_file(&temp);
                let _ = fs::remove_file(&rollback);
                return Err(format!(
                    "replace file failed: {first_error}; remove existing file failed: {remove_error}"
                ));
            }
            if let Err(rename_error) = fs::rename(&temp, path) {
                let restore_error = if had_original {
                    fs::copy(&rollback, path).err()
                } else {
                    None
                };
                let _ = fs::remove_file(&temp);
                let _ = fs::remove_file(&rollback);
                return Err(match restore_error {
                    Some(error) => format!(
                        "replace file failed: {rename_error}; restoring original failed: {error}"
                    ),
                    None => format!("replace file failed: {rename_error}"),
                });
            }
            let _ = fs::remove_file(&rollback);
        }
        #[cfg(not(windows))]
        {
            let _ = fs::remove_file(&temp);
            return Err(format!("replace file failed: {first_error}"));
        }
    }
    Ok(())
}

fn transaction_backup(path: &Path, label: &str) -> Result<PathBuf, String> {
    let backup = path.with_extension(format!("bak-{}", now_ms()));
    fs::copy(path, &backup).map_err(|error| format!("backup {label} failed: {error}"))?;
    Ok(backup)
}

fn restore_transaction(path: &Path, backup: &Path) -> Result<(), String> {
    fs::copy(backup, path)
        .map(|_| ())
        .map_err(|error| format!("restoring backup failed: {error}"))
}

fn transaction_failure(path: &Path, backup: &Path, message: impl Into<String>) -> String {
    let message = message.into();
    match restore_transaction(path, backup) {
        Ok(()) => format!("{message}; original file restored from backup."),
        Err(error) => format!("{message}; {error}"),
    }
}

fn replace_active(profile: &ProfileDto, active_mods: &[String]) -> Result<SaveResult, String> {
    if !profile.writable {
        return Err("Steam/Cloud profiles are read-only.".into());
    }
    if is_game_running() {
        return Err("Exit ETS2 or ATS before saving the profile.".into());
    }
    let path = profile_sii(Path::new(&profile.folder));
    let original = fs::read(&path).map_err(|e| format!("read profile failed: {e}"))?;
    let plain = decode_scsc_or_plain(&original)?;
    let text = String::from_utf8(plain).map_err(|e| format!("profile is not valid UTF-8: {e}"))?;
    if !text.contains("SiiNunit") || !text.contains("profile") {
        return Err("Profile is encrypted or not a plaintext SII file.".into());
    }
    let original_lines: Vec<String> = text
        .replace("\r\n", "\n")
        .split('\n')
        .map(str::to_string)
        .collect();
    let positions: HashSet<usize> = original_lines
        .iter()
        .enumerate()
        .filter_map(|(i, line)| line.trim_start().starts_with("active_mods[").then_some(i))
        .collect();
    let indent = original_lines
        .iter()
        .find(|line| line.trim_start().starts_with("active_mods["))
        .map(|line| {
            line.chars()
                .take_while(|c| c.is_whitespace())
                .collect::<String>()
        })
        .unwrap_or_else(|| "    ".into());
    let mut lines: Vec<String> = original_lines
        .into_iter()
        .enumerate()
        .filter_map(|(index, line)| (!positions.contains(&index)).then_some(line))
        .collect();
    if let Some(index) = lines
        .iter()
        .position(|line| line.trim_start().starts_with("active_mods:"))
    {
        lines[index] = format!("{indent}active_mods: {}", active_mods.len());
    } else {
        let at = lines
            .iter()
            .position(|x| x.trim() == "}")
            .unwrap_or(lines.len());
        lines.insert(at, format!("{indent}active_mods: {}", active_mods.len()));
    }
    let insert_at = lines
        .iter()
        .position(|line| line.trim_start().starts_with("active_mods:"))
        .map(|index| index + 1)
        .unwrap_or(lines.len());
    let new_lines = active_mods
        .iter()
        .enumerate()
        .map(|(index, value)| format!("{indent}active_mods[{index}]: \"{}\"", escape_sii(value)));
    lines.splice(insert_at..insert_at, new_lines);
    let plain = lines.join("\n").into_bytes();
    let encoded = if original.starts_with(b"ScsC") {
        encode_scsc(&plain)?
    } else {
        plain
    };
    let backup = transaction_backup(&path, "profile")?;
    if let Err(write_error) = atomic_write(&path, &encoded) {
        return Err(transaction_failure(&path, &backup, write_error));
    }
    let verify = match read_sii(&path) {
        Ok(value) => value,
        Err(read_error) => {
            return Err(transaction_failure(
                &path,
                &backup,
                format!("profile write verification read failed: {read_error}"),
            ));
        }
    };
    if parse_active_mods(&verify) != active_mods {
        return Err(transaction_failure(
            &path,
            &backup,
            "active_mods write verification failed",
        ));
    }
    Ok(SaveResult {
        success: true,
        message: format!("Saved {} active mods.", active_mods.len()),
    })
}

fn is_game_running() -> bool {
    if !cfg!(target_os = "windows") {
        return false;
    }
    game_running_checked().unwrap_or(true)
}

fn game_running_checked() -> Result<bool, String> {
    let mut command = std::process::Command::new("tasklist");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    command
        .args(["/FO", "CSV", "/NH"])
        .output()
        .map_err(|e| format!("Could not check game process: {e}"))
        .and_then(|output| {
            if !output.status.success() {
                return Err("Could not check game process.".into());
            }
            let stdout = String::from_utf8_lossy(&output.stdout).to_ascii_lowercase();
            Ok(stdout.contains("\"eurotrucks2.exe\"") || stdout.contains("\"amtrucks.exe\""))
        })
}

#[cfg(feature = "desktop")]
#[tauri::command]
async fn category_list(state: State<'_, BackendState>) -> Result<categories::Snapshot, String> {
    let database = state
        .inner
        .lock()
        .map_err(|e| e.to_string())?
        .database_path
        .clone();
    tauri::async_runtime::spawn_blocking(move || {
        let mut db = open_db(&database)?;
        let legacy = cache_directory();
        let dotnet = std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .map(|root| root.join("ETS2ModManager").join("categories.json"));
        let warning =
            categories::import_legacy(&mut db, legacy.as_deref(), dotnet.as_deref()).err();
        let mut snapshot = categories::snapshot(&db)?;
        snapshot.warning = warning;
        Ok(snapshot)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[cfg(feature = "desktop")]
#[tauri::command]
async fn category_mutate(
    request: categories::Mutation,
    state: State<'_, BackendState>,
) -> Result<categories::Snapshot, String> {
    let database = state
        .inner
        .lock()
        .map_err(|e| e.to_string())?
        .database_path
        .clone();
    tauri::async_runtime::spawn_blocking(move || {
        categories::mutate(&mut open_db(&database)?, request)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[cfg(feature = "desktop")]
#[tauri::command]
async fn mod_directory_status(
    state: State<'_, BackendState>,
) -> Result<mod_directory::Status, String> {
    let root = state
        .inner
        .lock()
        .map_err(|e| e.to_string())?
        .paths
        .mod_root
        .clone();
    tauri::async_runtime::spawn_blocking(move || mod_directory::status(&root))
        .await
        .map_err(|e| e.to_string())?
}

#[cfg(feature = "desktop")]
#[tauri::command]
async fn mod_directory_pick() -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(|| {
        #[cfg(windows)]
        return Ok(rfd::FileDialog::new()
            .pick_folder()
            .map(|path| path.to_string_lossy().into_owned()));
        #[cfg(not(windows))]
        Ok(None)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[cfg(feature = "desktop")]
#[tauri::command(rename_all = "camelCase")]
async fn mod_directory_change(
    operation: String,
    target: String,
    app: AppHandle,
    state: State<'_, BackendState>,
) -> Result<mod_directory::Outcome, String> {
    let (root, database) = {
        let backend = state.inner.lock().map_err(|e| e.to_string())?;
        (
            backend.paths.mod_root.clone(),
            backend.database_path.clone(),
        )
    };
    tauri::async_runtime::spawn_blocking(move || {
        let guard = || {
            if game_running_checked()? {
                return Err("Close ETS2 / ATS before changing the Mod directory.".into());
            }
            Ok(())
        };
        if operation == "recover" {
            guard()?;
            return mod_directory::recover(&root, &database);
        }
        let mut last_event = std::time::Instant::now() - std::time::Duration::from_secs(1);
        mod_directory::change(
            &root,
            Path::new(&target),
            &operation,
            &database,
            guard,
            |progress| {
                if last_event.elapsed().as_millis() >= 100
                    || matches!(progress.phase.as_str(), "switch" | "complete")
                {
                    let _ = app.emit_to("main", "ets2-directory-progress", &progress);
                    last_event = std::time::Instant::now();
                }
            },
        )
    })
    .await
    .map_err(|e| format!("Directory worker failed: {e}"))?
}

fn find_profile(paths: &Paths, profile_id: &str) -> Option<ProfileDto> {
    all_profiles(paths).into_iter().find(|p| p.id == profile_id)
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn profile_list(state: State<'_, BackendState>) -> Result<Vec<ProfileDto>, String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    Ok(all_profiles(&backend.paths))
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn profile_read_active(
    profile_id: String,
    state: State<'_, BackendState>,
) -> Result<Vec<String>, String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    let profile = find_profile(&backend.paths, &profile_id).ok_or("Profile not found.")?;
    active_for_profile(&profile)
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn profile_write_active(
    request: WriteActiveRequest,
    state: State<'_, BackendState>,
) -> Result<SaveResult, String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    let profile = find_profile(&backend.paths, &request.profile_id).ok_or("Profile not found.")?;
    replace_active(&profile, &request.active_mods)
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn mod_list(profile_id: String, state: State<'_, BackendState>) -> Result<Vec<ModDto>, String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    let mut db = open_db(&backend.database_path)?;
    let mut mods = dedupe_mods(load_cached(&db)?);
    refresh_cached_workshop_titles(&mut db, &mut mods, &backend.paths)?;
    if let Some(profile) = find_profile(&backend.paths, &profile_id) {
        let active = active_for_profile(&profile)?;
        apply_enabled(&mut mods, &active);
        let mut by_key: HashMap<String, ModDto> =
            mods.into_iter().fold(HashMap::new(), |mut rows, row| {
                let key = canonical_package(&row.package_name);
                match rows.get(&key) {
                    Some(existing) if !is_workshop(existing) && is_workshop(&row) => {}
                    _ => {
                        rows.insert(key, row);
                    }
                }
                rows
            });
        let mut ordered = Vec::with_capacity(by_key.len());
        for package in profile_order_to_ui(&active) {
            let key = by_key
                .keys()
                .find(|key| rows_match(key, &package))
                .cloned()
                .unwrap_or_else(|| canonical_package(&package));
            if let Some(row) = by_key.remove(&key) {
                let mut row = row;
                if let Some(label) = workshop_label_from_value(&package) {
                    let current = row.display_name.trim();
                    if current.is_empty()
                        || current == row.id.trim()
                        || current.chars().all(|value| value.is_ascii_digit())
                    {
                        row.display_name = label;
                    }
                }
                ordered.push(row);
            } else {
                let workshop_path = workshop_id_from_value(&package).and_then(|id| {
                    backend
                        .paths
                        .workshop_roots
                        .iter()
                        .map(|root| root.join(&id))
                        .find(|path| path.is_dir())
                });
                let display_name = workshop_id_from_value(&package)
                    .and_then(|id| workshop_log_title(&id, &backend.paths))
                    .or_else(|| workshop_cached_title(&package))
                    .or_else(|| workshop_label_from_value(&package))
                    .unwrap_or_else(|| package.clone());
                ordered.push(ModDto {
                    id: package.clone(),
                    package_name: package.clone(),
                    path: workshop_path
                        .as_deref()
                        .map(normalize_path)
                        .unwrap_or_default(),
                    package_type: if workshop_path.is_some()
                        || workshop_id_from_value(&package).is_some()
                    {
                        "workshop".into()
                    } else {
                        "unknown".into()
                    },
                    display_name,
                    author: String::new(),
                    version: String::new(),
                    size: 0,
                    modified_ms: 0,
                    enabled: true,
                    category: "unknown".into(),
                    fingerprint: 0,
                });
            }
        }
        let mut remaining: Vec<ModDto> = by_key.into_values().collect();
        remaining.sort_by(|a, b| {
            a.display_name
                .to_lowercase()
                .cmp(&b.display_name.to_lowercase())
                .then_with(|| a.path.to_lowercase().cmp(&b.path.to_lowercase()))
        });
        ordered.extend(remaining);
        return Ok(ordered);
    }
    Ok(mods)
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
async fn mod_media(
    request: ModMediaRequest,
    state: State<'_, BackendState>,
) -> Result<ModMediaDto, String> {
    mod_media_batch(vec![request], state)
        .await?
        .into_iter()
        .next()
        .ok_or_else(|| "Media result missing.".into())
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
async fn mod_media_batch(
    requests: Vec<ModMediaRequest>,
    state: State<'_, BackendState>,
) -> Result<Vec<ModMediaDto>, String> {
    if requests.len() > 8 {
        return Err("Media batch must contain at most 8 packages.".into());
    }
    let database = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?
        .database_path
        .clone();
    #[cfg(feature = "desktop")]
    {
        return tauri::async_runtime::spawn_blocking(move || {
            mod_media_batch_impl(&database, requests)
        })
        .await
        .map_err(|e| format!("media worker failed: {e}"))?;
    }
    #[cfg(not(feature = "desktop"))]
    mod_media_batch_impl(&database, requests)
}

fn mod_media_batch_impl(
    database: &Path,
    requests: Vec<ModMediaRequest>,
) -> Result<Vec<ModMediaDto>, String> {
    let _directory_lock = mod_directory::read_lock(
        &database
            .parent()
            .ok_or("Database directory is missing.")?
            .join("mod"),
    )?;
    let connection = open_db(database)?;
    let catalog = load_cached(&connection)?;
    requests
        .into_iter()
        .map(|request| {
            let Some(row) = catalog
                .iter()
                .find(|row| row.path == request.path && row.id == request.mod_id)
            else {
                return Ok(ModMediaDto {
                    mod_id: request.mod_id,
                    icon_url: None,
                    preview_url: None,
                });
            };
            cached_mod_media(&connection, row, resolve_mod_media)
        })
        .collect()
}

fn warm_mod_media_with_progress<F>(
    database: &Path,
    cancelled: &AtomicBool,
    progress: &mut F,
) -> Result<(), String>
where
    F: FnMut(&str, usize, usize, &str, &Path),
{
    let connection = open_db(database)?;
    let catalog = load_cached(&connection)?;
    let total = catalog.len();
    let mut misses = Vec::new();
    for row in &catalog {
        if !media_cache_hit(&connection, row)? {
            misses.push(row);
        }
    }
    // A fully persisted media index should make startup effectively a DB read.
    // Do not replay one progress event per package when there is no work.
    if misses.is_empty() {
        progress("media-cached", total, total, "", database);
        return Ok(());
    }
    let miss_count = misses.len();
    progress("media", 0, miss_count, "", database);
    for (index, row) in misses.into_iter().enumerate() {
        if cancelled.load(Ordering::Relaxed) {
            return Err("Scan cancelled.".into());
        }
        progress(
            "media",
            index,
            miss_count,
            &row.display_name,
            Path::new(&row.path),
        );
        // cached_mod_media performs the fingerprint/resolver-version lookup
        // first, so initialized packages never reopen their archive or invoke
        // the extractor again.
        cached_mod_media(&connection, row, resolve_mod_media)?;
    }
    progress("media", miss_count, miss_count, "", database);
    Ok(())
}

fn is_workshop(row: &ModDto) -> bool {
    row.package_type.eq_ignore_ascii_case("workshop")
        || row.path.to_ascii_lowercase().contains("workshop")
        || workshop_id_from_value(&row.package_name).is_some()
        || workshop_id_from_value(&row.id).is_some()
}

fn dedupe_mods(mods: Vec<ModDto>) -> Vec<ModDto> {
    let mut by_key: HashMap<String, ModDto> = HashMap::new();
    for row in mods {
        let key = canonical_package(&row.package_name);
        match by_key.get(&key) {
            Some(existing) if !is_workshop(existing) && is_workshop(&row) => {
                by_key.insert(key, row);
            }
            Some(existing) if is_workshop(existing) && !is_workshop(&row) => {
                // Keep the Workshop record when a duplicate local filename
                // happens to normalize to the same package key.
            }
            None => {
                by_key.insert(key, row);
            }
            _ => {}
        }
    }
    let mut result: Vec<ModDto> = by_key.into_values().collect();
    result.sort_by(|a, b| {
        a.display_name
            .to_lowercase()
            .cmp(&b.display_name.to_lowercase())
            .then_with(|| a.path.to_lowercase().cmp(&b.path.to_lowercase()))
    });
    result
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn mod_scan(state: State<'_, BackendState>) -> Result<ScanSummary, String> {
    let (paths, database_path, cancelled) = {
        let backend = state
            .inner
            .lock()
            .map_err(|_| "backend lock poisoned".to_string())?;
        (
            backend.paths.clone(),
            backend.database_path.clone(),
            Arc::clone(&backend.scan_cancelled),
        )
    };
    scan_mod_inputs(paths, database_path, cancelled)
}

fn scan_mod_inputs(
    paths: Paths,
    database_path: PathBuf,
    cancelled: Arc<AtomicBool>,
) -> Result<ScanSummary, String> {
    scan_mod_inputs_with_progress(paths, database_path, cancelled, &mut |_, _, _, _, _| {})
}

fn scan_mod_inputs_with_progress<F>(
    paths: Paths,
    database_path: PathBuf,
    cancelled: Arc<AtomicBool>,
    progress: &mut F,
) -> Result<ScanSummary, String>
where
    F: FnMut(&str, usize, usize, &str, &Path),
{
    let _directory_lock = mod_directory::read_lock(&paths.mod_root)?;
    cancelled.store(false, Ordering::Relaxed);
    progress("cache", 0, 0, "", &database_path);
    let mut db = open_db(&database_path)?;
    let mut discovered =
        discover_packages_with_progress(Some(&paths.mod_root), false, &cancelled, progress);
    discovered.extend(discover_workshop_packages_with_progress(
        &paths.workshop_roots,
        &cancelled,
        progress,
    ));
    if cancelled.load(Ordering::Relaxed) {
        return Err("Scan cancelled.".into());
    }
    sync_index_with_progress(&mut db, &discovered, progress)
}

fn startup_incremental_scan<F>(
    paths: Paths,
    database_path: PathBuf,
    cancelled: Arc<AtomicBool>,
    progress: &mut F,
) -> Result<ScanSummary, String>
where
    F: FnMut(&str, usize, usize, &str, &Path),
{
    let _directory_lock = mod_directory::read_lock(&paths.mod_root)?;
    cancelled.store(false, Ordering::Relaxed);
    let mut db = open_db(&database_path)?;
    backfill_mod_header_state(&db)?;
    let cached = load_cached(&db)?;
    let initialized = db
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM mod_index_state WHERE id = 1)",
            [],
            |row| row.get::<_, i64>(0),
        )
        .map(|value| value != 0)
        .unwrap_or(false);
    if !initialized {
        return scan_mod_inputs_with_progress(paths, database_path, cancelled, progress);
    }
    progress("cache", cached.len(), cached.len(), "", &database_path);
    let mut candidates = shallow_package_candidates(Some(&paths.mod_root), false, &cancelled);
    for root in &paths.workshop_roots {
        candidates.extend(shallow_package_candidates(Some(root), true, &cancelled));
    }
    let cached_by_path: HashMap<String, ModDto> =
        cached.into_iter().map(|m| (m.path.clone(), m)).collect();
    let candidate_paths = candidates
        .iter()
        .map(|candidate| normalize_path(&candidate.path))
        .collect::<HashSet<_>>();
    let all_cached = candidate_paths.len() == cached_by_path.len()
        && candidate_paths
            .iter()
            .all(|path| cached_by_path.contains_key(path))
        && candidates.iter().all(|candidate| {
            let path = normalize_path(&candidate.path);
            // Compare against the persisted package signature itself. The
            // header table was introduced after older indexes and may contain
            // directory metadata rather than the bounded info-file signature;
            // using it as the source of truth would force a full re-scan on
            // every restart. The package row is written from the same
            // `ShallowCandidate` values during sync and is therefore the
            // durable compatibility point.
            cached_by_path
                .get(&path)
                .map(|row| {
                    row.size == candidate.size
                        && row.modified_ms == candidate.modified_ms
                        && Path::new(&row.path).is_dir() == candidate.is_directory
                        && !metadata_needs_refresh(row)
                })
                .unwrap_or(false)
        });
    if all_cached {
        progress(
            "cached",
            candidates.len(),
            candidates.len(),
            "",
            &database_path,
        );
        return Ok(ScanSummary {
            total: candidates.len(),
            added: 0,
            updated: 0,
            removed: 0,
            inspected: 0,
            elapsed_ms: 0,
        });
    }
    let mut discovered = Vec::with_capacity(candidates.len());
    let mut inspected = 0usize;
    for (index, candidate) in candidates.iter().enumerate() {
        let path = normalize_path(&candidate.path);
        let unchanged = cached_by_path.get(&path).is_some_and(|row| {
            row.size == candidate.size
                && row.modified_ms == candidate.modified_ms
                && Path::new(&row.path).is_dir() == candidate.is_directory
        });
        if unchanged {
            if let Some(row) = cached_by_path.get(&path) {
                progress(
                    "cached",
                    index + 1,
                    candidates.len(),
                    &row.display_name,
                    &candidate.path,
                );
                discovered.push(row.clone());
                continue;
            }
        }
        inspected += 1;
        if let Some(row) = discover_single_candidate(candidate, &cancelled, progress) {
            discovered.push(row);
        }
    }
    if cancelled.load(Ordering::Relaxed) {
        return Err("Scan cancelled.".into());
    }
    let summary = sync_index_with_progress(&mut db, &discovered, progress)?;
    Ok(ScanSummary {
        inspected,
        ..summary
    })
}

#[cfg(feature = "desktop")]
#[tauri::command(rename_all = "camelCase")]
async fn mod_initialize(
    app: AppHandle,
    state: State<'_, BackendState>,
) -> Result<ScanSummary, String> {
    let (paths, database_path, cancelled) = {
        let backend = state
            .inner
            .lock()
            .map_err(|_| "backend lock poisoned".to_string())?;
        (
            backend.paths.clone(),
            backend.database_path.clone(),
            Arc::clone(&backend.scan_cancelled),
        )
    };
    tauri::async_runtime::spawn_blocking(move || {
        // Interrupted migrations must not trap the user in the initializer:
        // open the cached workspace so directory recovery remains accessible.
        if mod_directory::status(&paths.mod_root)?.recovery_pending {
            let cached = load_cached(&open_db(&database_path)?)?;
            return Ok(ScanSummary {
                total: cached.len(),
                added: 0,
                updated: 0,
                removed: 0,
                inspected: 0,
                elapsed_ms: 0,
            });
        }
        let summary = startup_incremental_scan(
            paths,
            database_path.clone(),
            Arc::clone(&cancelled),
            &mut |phase, current, total, name, path| {
                let _ = app.emit_to(
                    "initializer",
                    "ets2-scan-progress",
                    ScanProgress {
                        phase: phase.into(),
                        current,
                        total,
                        name: name.into(),
                        path: if path.as_os_str().is_empty() {
                            String::new()
                        } else {
                            normalize_path(path)
                        },
                    },
                );
            },
        )?;
        warm_mod_media_with_progress(
            &database_path,
            &cancelled,
            &mut |phase, current, total, name, path| {
                let _ = app.emit_to(
                    "initializer",
                    "ets2-scan-progress",
                    ScanProgress {
                        phase: phase.into(),
                        current,
                        total,
                        name: name.into(),
                        path: if path.as_os_str().is_empty() {
                            String::new()
                        } else {
                            normalize_path(path)
                        },
                    },
                );
            },
        )?;
        Ok(summary)
    })
    .await
    .map_err(|error| format!("initialization worker failed: {error}"))?
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn mod_cancel(state: State<'_, BackendState>) -> Result<(), String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    backend.scan_cancelled.store(true, Ordering::Relaxed);
    Ok(())
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn mod_set_enabled(
    profile_id: String,
    package_name: String,
    enabled: bool,
    state: State<'_, BackendState>,
) -> Result<Vec<String>, String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    let profile = find_profile(&backend.paths, &profile_id).ok_or("Profile not found.")?;
    let mut active = active_for_profile(&profile)?;
    let key = canonical_package(&package_name);
    active.retain(|x| canonical_package(x) != key);
    if enabled {
        active.push(package_name);
    }
    replace_active(&profile, &active)?;
    Ok(active)
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn mod_move(request: MoveRequest, state: State<'_, BackendState>) -> Result<SaveResult, String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    let profile = find_profile(&backend.paths, &request.profile_id).ok_or("Profile not found.")?;
    replace_active(&profile, &request.active_mods)
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn mod_open_location(
    package_name: String,
    path: String,
    package_type: String,
) -> Result<(), String> {
    let workshop = package_type.eq_ignore_ascii_case("workshop")
        || workshop_id_from_value(&package_name).is_some()
        || path.to_ascii_lowercase().contains("workshop");
    if workshop {
        let id = workshop_id_from_value(&package_name)
            .or_else(|| workshop_id_from_value(&path))
            .ok_or("Workshop ID is unavailable.")?;
        #[cfg(windows)]
        {
            let mut command = std::process::Command::new("cmd");
            command.args([
                "/C",
                "start",
                "",
                &format!("https://steamcommunity.com/sharedfiles/filedetails/?id={id}"),
            ]);
            hide_child_process(&mut command);
            command
                .status()
                .map_err(|error| format!("open Steam Workshop page failed: {error}"))?;
            return Ok(());
        }
        #[cfg(not(windows))]
        {
            let _ = id;
            return Err("Opening Steam Workshop pages is only supported on Windows.".into());
        }
    }
    let target = PathBuf::from(path);
    if !target.exists() {
        return Err("Mod path does not exist.".into());
    }
    #[cfg(windows)]
    {
        let mut command = std::process::Command::new("explorer.exe");
        if target.is_file() {
            command.arg("/select,").arg(&target);
        } else {
            command.arg(&target);
        }
        hide_child_process(&mut command);
        command
            .status()
            .map_err(|error| format!("open Mod location failed: {error}"))?;
        Ok(())
    }
    #[cfg(not(windows))]
    {
        let _ = target;
        Err("Opening Mod locations is only supported on Windows.".into())
    }
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn profile_open_location(profile_id: String, state: State<'_, BackendState>) -> Result<(), String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    let profile = find_profile(&backend.paths, &profile_id).ok_or("Profile not found.")?;
    let target = PathBuf::from(&profile.folder);
    if !target.is_dir() {
        return Err("Profile directory does not exist.".into());
    }
    #[cfg(windows)]
    {
        let mut command = std::process::Command::new("explorer.exe");
        command.arg(&target);
        hide_child_process(&mut command);
        command
            .status()
            .map_err(|error| format!("open Profile directory failed: {error}"))?;
        Ok(())
    }
    #[cfg(not(windows))]
    {
        let _ = target;
        Err("Opening Profile directories is only supported on Windows.".into())
    }
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn preset_list(
    profile_id: String,
    state: State<'_, BackendState>,
) -> Result<Vec<PresetDto>, String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    let db = open_db(&backend.database_path)?;
    let mut statement = db
        .prepare("SELECT name, active_mods_json FROM preset WHERE profile_id = ?1 ORDER BY name COLLATE NOCASE")
        .map_err(|e| format!("query presets failed: {e}"))?;
    let rows = statement
        .query_map(params![profile_id], |row| {
            let json: String = row.get(1)?;
            Ok(PresetDto {
                name: row.get(0)?,
                active_mods: serde_json::from_str(&json).unwrap_or_default(),
            })
        })
        .map_err(|e| format!("read presets failed: {e}"))?;
    rows.map(|row| row.map_err(|e| format!("read preset failed: {e}")))
        .collect()
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn preset_save(request: PresetRequest, state: State<'_, BackendState>) -> Result<(), String> {
    let active_mods = request.active_mods.ok_or("active_mods is required.")?;
    if request.name.trim().is_empty() {
        return Err("Preset name is required.".into());
    }
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    let db = open_db(&backend.database_path)?;
    db.execute(
        "INSERT INTO preset(profile_id, name, active_mods_json) VALUES (?1, ?2, ?3)
         ON CONFLICT(profile_id, name) DO UPDATE SET active_mods_json=excluded.active_mods_json",
        params![
            request.profile_id,
            request.name.trim(),
            serde_json::to_string(&active_mods).map_err(|e| e.to_string())?
        ],
    )
    .map_err(|e| format!("save preset failed: {e}"))?;
    Ok(())
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn preset_load(
    request: PresetRequest,
    state: State<'_, BackendState>,
) -> Result<Vec<String>, String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    let db = open_db(&backend.database_path)?;
    db.query_row(
        "SELECT active_mods_json FROM preset WHERE profile_id = ?1 AND name = ?2",
        params![request.profile_id, request.name.trim()],
        |row| row.get::<_, String>(0),
    )
    .optional()
    .map_err(|e| format!("load preset failed: {e}"))?
    .map(|json| serde_json::from_str(&json).unwrap_or_default())
    .ok_or_else(|| "Preset not found.".into())
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn preset_delete(request: PresetRequest, state: State<'_, BackendState>) -> Result<bool, String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    let db = open_db(&backend.database_path)?;
    let changed = db
        .execute(
            "DELETE FROM preset WHERE profile_id = ?1 AND name = ?2",
            params![request.profile_id, request.name.trim()],
        )
        .map_err(|e| format!("delete preset failed: {e}"))?;
    Ok(changed > 0)
}

fn list_local_saves(profile: &ProfileDto) -> Vec<SaveSlotDto> {
    if !profile.writable {
        return Vec::new();
    }
    let root = Path::new(&profile.folder).join("save");
    let mut rows: Vec<(u8, String, String, SaveSlotDto)> = Vec::new();
    let Ok(entries) = fs::read_dir(root) else {
        return Vec::new();
    };
    for entry in entries.flatten() {
        let folder = entry.path();
        let game = folder.join("game.sii");
        if !folder.is_dir() || !game.is_file() {
            continue;
        }
        let info = folder.join("info.sii");
        let display_name = read_sii(&info)
            .ok()
            .and_then(|text| parse_field(&text, "name"))
            .filter(|x| !x.trim().is_empty())
            .unwrap_or_else(|| {
                folder
                    .file_name()
                    .and_then(|x| x.to_str())
                    .unwrap_or_default()
                    .into()
            });
        let slot_id = folder
            .file_name()
            .and_then(|x| x.to_str())
            .unwrap_or_default()
            .to_string();
        let rank = if slot_id.eq_ignore_ascii_case("autosave") {
            1
        } else if slot_id.to_ascii_lowercase().starts_with("autosave") {
            2
        } else {
            0
        };
        let last_modified_ms = fs::metadata(&game)
            .and_then(|metadata| metadata.modified())
            .ok()
            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
            .map(|duration| duration.as_millis() as i64)
            .unwrap_or_default();
        rows.push((
            rank,
            display_name.to_lowercase(),
            slot_id.to_lowercase(),
            SaveSlotDto {
                profile_id: profile.id.clone(),
                slot_id,
                folder: normalize_path(&folder),
                game_sii: normalize_path(&game),
                display_name,
                last_modified_ms,
                profile_location: profile.location.clone(),
            },
        ));
    }
    rows.sort_by(|a, b| {
        a.0.cmp(&b.0)
            .then_with(|| a.1.cmp(&b.1))
            .then_with(|| a.2.cmp(&b.2))
    });
    rows.into_iter().map(|(_, _, _, row)| row).collect()
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn save_list_local(
    profile_id: String,
    state: State<'_, BackendState>,
) -> Result<Vec<SaveSlotDto>, String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    let profile = find_profile(&backend.paths, &profile_id).ok_or("Profile not found.")?;
    Ok(list_local_saves(&profile))
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn game_launch(state: State<'_, BackendState>) -> Result<SaveResult, String> {
    let _directory_lock = {
        let backend = state.inner.lock().map_err(|e| e.to_string())?;
        mod_directory::read_lock(&backend.paths.mod_root)?
    };
    let executable = {
        let backend = state.inner.lock().map_err(|_| "backend lock poisoned".to_string())?;
        backend.paths.game_executable.clone()
    }
    .ok_or_else(|| {
        "ETS2/ATS executable was not found. Install the game through Steam or configure the Steam library path."
            .to_string()
    })?;

    if !executable.is_file() {
        return Err(format!(
            "Game executable does not exist: {}",
            executable.display()
        ));
    }
    if is_game_running() {
        return Ok(SaveResult {
            success: true,
            message: "ETS2 or ATS is already running.".into(),
        });
    }

    let working_directory = executable
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .ok_or_else(|| "Could not determine the game working directory.".to_string())?;
    let mut command = std::process::Command::new(&executable);
    command.current_dir(&working_directory);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    command
        .spawn()
        .map_err(|error| format!("launch game failed: {error}"))?;
    Ok(SaveResult {
        success: true,
        message: format!("Started {}.", executable.display()),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn level_xp_uses_profile_formula_and_rejects_invalid_levels() {
        assert_eq!(level_xp(1).expect("level 1"), 0);
        assert_eq!(level_xp(2).expect("level 2"), 1_000);
        assert_eq!(level_xp(200).expect("level 200"), 19_900_000);
        assert!(level_xp(0).is_err());
        assert!(level_xp(201).is_err());
    }

    #[test]
    fn crash_log_analysis_matches_active_mod_and_keeps_evidence() {
        let dir = std::env::temp_dir().join(format!("ets2mm-crash-{}", now_ms()));
        fs::create_dir_all(&dir).expect("create temp dir");
        let log = dir.join("game.log.txt");
        fs::write(
            &log,
            "[error] Failed to load promods-eu-def-v282.scs: missing unit\n",
        )
        .expect("write log");
        let row = ModDto {
            id: "promods-eu-def-v282".into(),
            package_name: "promods-eu-def-v282".into(),
            path: "promods-eu-def-v282.scs".into(),
            package_type: "scs".into(),
            display_name: "ProMods Europe".into(),
            author: String::new(),
            version: String::new(),
            size: 1,
            modified_ms: 1,
            enabled: true,
            category: String::new(),
            fingerprint: 1,
        };
        let pair = CrashPairDto {
            crash_path: None,
            log_path: Some(normalize_path(&log)),
            source: Some("ets2".into()),
        };
        let (summary, evidence, issues) =
            analyze_crash_logs(&pair, &["promods-eu-def-v282".into()], &[row]);
        assert!(summary.contains("1"));
        assert!(!evidence.is_empty());
        assert_eq!(issues[0].display_name, "ProMods Europe");
        assert_eq!(issues[0].code, "LOG_ERROR_REFERENCE");
        assert_eq!(issues[0].severity, "yellow");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn crash_log_analysis_downgrades_warning_to_yellow() {
        let dir = std::env::temp_dir().join(format!("ets2mm-crash-warning-{}", now_ms()));
        fs::create_dir_all(&dir).expect("create temp dir");
        let log = dir.join("game.log.txt");
        fs::write(
            &log,
            "[warning] promods-eu-def-v282.scs is deprecated; fallback will be used\n",
        )
        .expect("write log");
        let row = ModDto {
            id: "promods-eu-def-v282".into(),
            package_name: "promods-eu-def-v282".into(),
            path: "promods-eu-def-v282.scs".into(),
            package_type: "scs".into(),
            display_name: "ProMods Europe".into(),
            author: String::new(),
            version: String::new(),
            size: 1,
            modified_ms: 1,
            enabled: true,
            category: String::new(),
            fingerprint: 1,
        };
        let pair = CrashPairDto {
            crash_path: None,
            log_path: Some(normalize_path(&log)),
            source: Some("ets2".into()),
        };
        let (_, _, issues) =
            analyze_crash_logs(&pair, &["promods-eu-def-v282".into()], &[row]);
        assert_eq!(issues.len(), 1);
        assert_eq!(issues[0].code, "LOG_WARNING_REFERENCE");
        assert_eq!(issues[0].severity, "yellow");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn crash_log_analysis_does_not_report_unrelated_lines() {
        let dir = std::env::temp_dir().join(format!("ets2mm-crash-empty-{}", now_ms()));
        fs::create_dir_all(&dir).expect("create temp dir");
        let log = dir.join("game.log.txt");
        fs::write(&log, "[sys] Process manager shutdown\n").expect("write log");
        let pair = CrashPairDto {
            crash_path: None,
            log_path: Some(normalize_path(&log)),
            source: Some("ets2".into()),
        };
        let (_, _, issues) =
            analyze_crash_logs(&pair, &["some-mod".into()], &[]);
        assert!(issues.is_empty());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn scsc_roundtrip_preserves_profile_text() {
        let plain = b"SiiNunit\n{\n profile : .profile {\n  active_mods: 2\n  active_mods[0]: \"high\"\n  active_mods[1]: \"low\"\n }\n}\n";
        let encoded = encode_scsc(plain).expect("encode");
        assert!(encoded.starts_with(b"ScsC"));
        assert_eq!(decode_scsc_or_plain(&encoded).expect("decode"), plain);
    }

    #[test]
    fn scsc_roundtrip_rejects_wrong_declared_size() {
        let plain = b"BSII\x03\0\0\0payload";
        let mut encoded = encode_scsc(plain).expect("encode");
        encoded[52..56].copy_from_slice(&(plain.len() as u32 + 1).to_le_bytes());
        let error = decode_scsc_or_plain(&encoded).expect_err("size mismatch");
        assert!(error.contains("size mismatch"));
    }

    #[test]
    fn non_zip_scs_is_rejected_before_zip_probe() {
        let path = std::env::temp_dir().join(format!(
            "ets2modmanager-hashfs-{}-{}.scs",
            now_ms(),
            std::process::id()
        ));
        fs::write(&path, b"SCS#\x01\0\0\0").expect("write hashfs header");
        assert!(!is_zip_archive(&path));
        assert!(read_zip_entry_bytes(&path, "manifest.sii").is_none());
        let _ = fs::remove_file(path);
    }

    #[test]
    fn active_mod_parser_keeps_game_order() {
        let text = "profile : .profile {\n active_mods[0]: \"high\"\n active_mods[1]: \"low\"\n}";
        assert_eq!(parse_active_mods(text), ["high", "low"]);
        assert_eq!(
            profile_order_to_ui(&parse_active_mods(text)),
            ["low", "high"]
        );
    }

    #[test]
    fn workshop_package_names_are_classified_as_workshop_without_local_path() {
        let row = ModDto {
            id: "mod_workshop_package.00000000B59F7017".into(),
            package_name: "mod_workshop_package.00000000B59F7017|Real traffic lights".into(),
            path: String::new(),
            package_type: "unknown".into(),
            display_name: "Real traffic lights".into(),
            author: String::new(),
            version: String::new(),
            size: 0,
            modified_ms: 0,
            enabled: true,
            category: "unknown".into(),
            fingerprint: 0,
        };
        assert!(is_workshop(&row));
        assert_eq!(
            workshop_id_from_value(&row.package_name).as_deref(),
            Some("3047125015")
        );
    }

    #[test]
    fn workshop_active_matching_uses_id_and_display_name_aliases() {
        let row = ModDto {
            id: "mod_workshop_package.00000000B59F7017".into(),
            package_name: "real_traffic_lights".into(),
            path: "workshop/3047125015".into(),
            package_type: "workshop".into(),
            display_name: "Real traffic lights".into(),
            author: String::new(),
            version: String::new(),
            size: 0,
            modified_ms: 0,
            enabled: false,
            category: "unknown".into(),
            fingerprint: 0,
        };
        assert!(row_matches_active(&row, "mod_workshop_package.00000000B59F7017"));
        assert!(row_matches_active(&row, "3047125015"));
        assert!(row_matches_active(&row, "Real traffic lights"));
        let mut rows = vec![row];
        apply_enabled(&mut rows, &["mod_workshop_package.00000000B59F7017".into()]);
        assert!(rows[0].enabled);
    }

    #[test]
    fn sii_unescape_decodes_utf8_hex_sequences() {
        assert_eq!(
            unescape_sii(r"\xe5\xa7\xac\xe9\x87\x8e\xe6\x98\x9f\xe5\xa5\x8f"),
            "姬野星奏"
        );
        assert_eq!(unescape_sii(r#"hello \"world\""#), "hello \"world\"");
    }

    #[test]
    fn profile_folder_name_decodes_hex_fallback() {
        assert_eq!(
            decode_profile_folder_name("E5A7ACE9878EE6989FE5A58F"),
            Some("姬野星奏".into())
        );
        assert_eq!(decode_profile_folder_name("not-a-profile"), None);
    }

    #[test]
    fn profile_catalog_only_exposes_local_profiles() {
        let root = std::env::temp_dir().join(format!("ets2modmanager-profiles-{}", now_ms()));
        let local = root.join("profiles").join("E5A7ACE9878EE6989FE5A58F");
        let steam = root.join("steam_profiles").join("steam-only");
        let cloud = root.join("cloud_profiles").join("cloud-only");
        for folder in [&local, &steam, &cloud] {
            fs::create_dir_all(folder).expect("create profile folder");
            fs::write(
                folder.join("profile.sii"),
                "SiiNunit\n{\n profile : .profile {\n  profile_name: \"Local\"\n }\n}\n",
            )
            .expect("write profile");
        }
        let paths = Paths {
            mod_root: root.join("mod"),
            game_root: root.clone(),
            profiles_root: root.join("profiles"),
            steam_profiles_root: Some(root.join("steam_profiles")),
            cloud_profiles_root: Some(root.join("cloud_profiles")),
            workshop_roots: Vec::new(),
            game_executable: None,
        };
        let profiles = all_profiles(&paths);
        let _ = fs::remove_dir_all(&root);
        assert_eq!(profiles.len(), 1);
        assert_eq!(profiles[0].location, "local");
        assert_eq!(profiles[0].name, "Local");
    }

    #[test]
    fn canonical_package_handles_storage_aliases() {
        assert_eq!(
            canonical_package("mod_workshop_package.0000002A|workshop"),
            "mod_workshop_package.0000002a"
        );
        assert_eq!(canonical_package("demo_local"), "demo");
        assert_eq!(canonical_package("demo_copy12"), "demo");
    }

    #[test]
    fn steam_library_parser_unescapes_vdf_paths() {
        let paths = parse_steam_library_paths(
            r#"
            "libraryfolders"
            {
                "0" { "path" "C:\\Program Files (x86)\\Steam" }
                "1" { "path" "E:\\SteamLibrary" }
            }
            "#,
        );
        assert_eq!(
            paths,
            vec![
                PathBuf::from(r"C:\Program Files (x86)\Steam"),
                PathBuf::from(r"E:\SteamLibrary")
            ]
        );
    }

    #[test]
    fn directory_info_signature_ignores_game_assets() {
        let root = std::env::temp_dir().join(format!("ets2modmanager-fingerprint-{}", now_ms()));
        fs::create_dir_all(&root).expect("create temp directory");
        fs::write(root.join("manifest.sii"), b"display_name: \"Demo\"").expect("write manifest");
        fs::create_dir_all(root.join("vehicle")).expect("create game directory");
        fs::write(root.join("vehicle/truck.pmd"), b"before").expect("write game asset");
        let before = directory_info_signature(&root, &AtomicBool::new(false));
        fs::write(root.join("vehicle/truck.pmd"), b"after").expect("rewrite game asset");
        let after = directory_info_signature(&root, &AtomicBool::new(false));
        let _ = fs::remove_dir_all(&root);
        assert_eq!(before.0, after.0);
        assert_eq!(before.1, after.1);
        assert_eq!(before.2, after.2);
    }

    #[test]
    fn directory_info_signature_changes_when_manifest_changes() {
        let root = std::env::temp_dir().join(format!("ets2modmanager-info-signature-{}", now_ms()));
        fs::create_dir_all(&root).expect("create temp directory");
        fs::write(root.join("manifest.sii"), b"display_name: \"One\"").expect("write manifest");
        let before = directory_info_signature(&root, &AtomicBool::new(false));
        fs::write(root.join("manifest.sii"), b"display_name: \"Two Longer\"")
            .expect("rewrite manifest");
        let after = directory_info_signature(&root, &AtomicBool::new(false));
        let _ = fs::remove_dir_all(&root);
        assert_ne!(before.2, after.2);
    }

    #[test]
    fn directory_info_signature_finds_nested_metadata() {
        let root = std::env::temp_dir().join(format!("ets2modmanager-nested-info-{}", now_ms()));
        let nested = root.join("metadata");
        fs::create_dir_all(&nested).expect("create nested metadata directory");
        fs::write(nested.join("manifest.sii"), b"display_name: \"One\"").expect("write manifest");
        let before = directory_info_signature(&root, &AtomicBool::new(false));
        fs::write(nested.join("manifest.sii"), b"display_name: \"Two\"").expect("rewrite manifest");
        let after = directory_info_signature(&root, &AtomicBool::new(false));
        let _ = fs::remove_dir_all(&root);
        assert_ne!(before.2, after.2);
    }

    #[test]
    fn localization_paths_only_accept_def_and_locale_segments() {
        assert!(is_definition_path("def/world/city.sii"));
        assert!(is_localization_path("locale/en_us/localization.sii"));
        assert!(!is_definition_path("city/world/city.sii"));
        assert!(!is_localization_path("localization/custom.sii"));
        assert!(is_localization_source_path("mod.scs::def/world/city.sii"));
        assert!(is_localization_source_path(
            "mod.scs::locale/en_us/localization.sii"
        ));
        assert!(!is_localization_source_path(
            "mod.scs::material/ui/icon.mat"
        ));
        assert!(is_localization_source_path_for_locale(
            "mod.scs::def/world/city.sii",
            "zh_cn"
        ));
        assert!(is_localization_source_path_for_locale(
            "mod.scs::locale/zh_cn/localization.sii",
            "zh_cn"
        ));
        assert!(!is_localization_source_path_for_locale(
            "mod.scs::locale/en_us/localization.sii",
            "zh_cn"
        ));
        assert!(!is_localization_source_path_for_locale(
            "mod.scs::material/ui/icon.mat",
            "zh_cn"
        ));
    }

    #[test]
    fn incremental_sync_preserves_unchanged_rows() {
        let mut connection = Connection::open_in_memory().expect("db");
        open_db_schema(&connection).expect("schema");
        let first = ModDto {
            id: "one".into(),
            package_name: "one".into(),
            path: "C:\\mods\\one.scs".into(),
            package_type: "scs".into(),
            display_name: "One".into(),
            author: String::new(),
            version: String::new(),
            size: 10,
            modified_ms: 20,
            enabled: false,
            category: "unknown".into(),
            fingerprint: 30,
        };
        let second = ModDto {
            id: "two".into(),
            package_name: "two".into(),
            path: "C:\\mods\\two.scs".into(),
            package_type: "scs".into(),
            display_name: "Two".into(),
            author: String::new(),
            version: String::new(),
            size: 11,
            modified_ms: 21,
            enabled: false,
            category: "unknown".into(),
            fingerprint: 31,
        };
        let first_summary =
            sync_index(&mut connection, &[first.clone(), second.clone()]).expect("first sync");
        assert_eq!(
            (
                first_summary.added,
                first_summary.updated,
                first_summary.removed
            ),
            (2, 0, 0)
        );
        let unchanged_summary =
            sync_index(&mut connection, &[first.clone(), second.clone()]).expect("unchanged sync");
        assert_eq!(
            (
                unchanged_summary.added,
                unchanged_summary.updated,
                unchanged_summary.removed,
                unchanged_summary.inspected
            ),
            (0, 0, 0, 0)
        );
        let initialized: i64 = connection
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM mod_index_state WHERE id = 1)",
                [],
                |row| row.get(0),
            )
            .expect("index state");
        assert_eq!(initialized, 1);
        let second_summary = sync_index(&mut connection, &[first.clone()]).expect("remove sync");
        assert_eq!(
            (
                second_summary.added,
                second_summary.updated,
                second_summary.removed
            ),
            (0, 0, 1)
        );
        let cached = load_cached(&connection).expect("cached");
        assert_eq!(cached.len(), 1);
        assert_eq!(cached[0].path, first.path);
    }

    #[test]
    fn media_cache_survives_restart_and_invalidates_changed_packages() {
        let root = std::env::temp_dir().join(format!("ets2-media-cache-{}", now_ms()));
        let database = root.join("index.db");
        let mut row = ModDto {
            id: "demo".into(),
            package_name: "demo".into(),
            path: "test-demo.scs".into(),
            package_type: "scs".into(),
            display_name: "Demo".into(),
            author: String::new(),
            version: String::new(),
            size: 10,
            modified_ms: 20,
            fingerprint: 30,
            enabled: false,
            category: String::new(),
        };
        let connection = open_db(&database).unwrap();
        let media = cached_mod_media(&connection, &row, |row| ModMediaDto {
            mod_id: row.id.clone(),
            icon_url: Some("data:image/png;base64,AQ==".into()),
            preview_url: Some("data:image/png;base64,Ag==".into()),
        })
        .unwrap();
        drop(connection);
        let connection = open_db(&database).unwrap();
        connection
            .execute("UPDATE mod_media_cache SET cached_at_ms=0", [])
            .unwrap();
        let cached = cached_mod_media(&connection, &row, |_| {
            panic!("unchanged artwork must be persisted")
        })
        .unwrap();
        assert_eq!(media.preview_url, cached.preview_url);
        assert_eq!(media.icon_url, cached.icon_url);
        row.fingerprint += 1;
        let changed = cached_mod_media(&connection, &row, |row| ModMediaDto {
            mod_id: row.id.clone(),
            icon_url: None,
            preview_url: None,
        })
        .unwrap();
        assert!(changed.preview_url.is_none());
        cached_mod_media(&connection, &row, |_| {
            panic!("missing artwork must not be repeatedly opened")
        })
        .unwrap();
        connection
            .execute("UPDATE mod_media_cache SET cached_at_ms=0", [])
            .unwrap();
        let refreshed = cached_mod_media(&connection, &row, |row| ModMediaDto {
            mod_id: row.id.clone(),
            icon_url: None,
            preview_url: Some("restored".into()),
        })
        .unwrap();
        assert_eq!(refreshed.preview_url.as_deref(), Some("restored"));
        let mut connection = connection;
        sync_index(&mut connection, &[]).unwrap();
        let remaining: i64 = connection
            .query_row("SELECT COUNT(*) FROM mod_media_cache", [], |row| row.get(0))
            .unwrap();
        assert_eq!(remaining, 0);
        drop(connection);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn directory_artwork_uses_manifest_icon_and_persisted_bytes() {
        let root = std::env::temp_dir().join(format!("ets2-media-image-{}", now_ms()));
        let package = root.join("demo");
        fs::create_dir_all(&package).unwrap();
        fs::write(package.join("manifest.sii"), "icon: \"custom.png\"").unwrap();
        fs::write(package.join("custom.png"), [1, 2, 3]).unwrap();
        let row = ModDto {
            id: "demo".into(),
            package_name: "demo".into(),
            path: normalize_path(&package),
            package_type: "directory".into(),
            display_name: "Demo".into(),
            author: String::new(),
            version: String::new(),
            size: 3,
            modified_ms: 1,
            fingerprint: 2,
            enabled: false,
            category: String::new(),
        };
        let connection = open_db(&root.join("index.db")).unwrap();
        let first = cached_mod_media(&connection, &row, resolve_mod_media).unwrap();
        assert_eq!(
            first.preview_url.as_deref(),
            Some("data:image/png;base64,AQID")
        );
        fs::remove_file(package.join("custom.png")).unwrap();
        let cached =
            cached_mod_media(&connection, &row, |_| panic!("must reuse image bytes")).unwrap();
        assert_eq!(first.preview_url, cached.preview_url);
        let changed = ModDto {
            fingerprint: 3,
            ..row
        };
        let refreshed = cached_mod_media(&connection, &changed, resolve_mod_media).unwrap();
        assert!(refreshed.preview_url.is_none());
        drop(connection);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn manifest_icon_path_is_normalized_without_fallback_names() {
        assert_eq!(manifest_icon_path("\\ui\\ai.jpg"), Some("ui/ai.jpg".into()));
        assert_eq!(manifest_icon_path("ai.jpg"), Some("ai.jpg".into()));
        assert_eq!(manifest_icon_path(""), None);
        assert_eq!(manifest_icon_path("../outside.jpg"), None);
        assert_eq!(manifest_icon_path("C:\\outside.jpg"), None);
    }

    #[test]
    fn external_tool_path_removes_windows_extended_prefix() {
        assert_eq!(
            external_tool_path(Path::new(r"\\?\H:\mods\demo.scs")),
            PathBuf::from(r"H:\mods\demo.scs")
        );
        assert_eq!(
            external_tool_path(Path::new(r"\\?\UNC\server\share\demo.scs")),
            PathBuf::from(r"\\server\share\demo.scs")
        );
    }

    #[test]
    fn directory_artwork_does_not_guess_when_manifest_has_no_icon() {
        let root = std::env::temp_dir().join(format!("ets2-media-no-fallback-{}", now_ms()));
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("icon.jpg"), [1, 2, 3]).unwrap();
        assert!(directory_image_path(&root, "").is_none());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn discovery_reports_current_package_before_reading_its_contents() {
        let root = std::env::temp_dir().join(format!("ets2-progress-discovery-{}", now_ms()));
        let package = root.join("demo_mod");
        fs::create_dir_all(&package).unwrap();
        let cancelled = AtomicBool::new(false);
        let mut events = Vec::new();
        let rows = discover_packages_with_progress(
            Some(&root),
            false,
            &cancelled,
            &mut |phase, current, total, name, path| {
                events.push((
                    phase.to_string(),
                    current,
                    total,
                    name.to_string(),
                    path.to_path_buf(),
                ));
                if name == "demo mod" {
                    // The signature must include a file created by the pre-read notification.
                    fs::write(path.join("test.txt"), "progress before read").unwrap();
                }
            },
        );
        assert_eq!(rows.len(), 1);
        // Discovery only fingerprints Mod information files; game assets are ignored.
        assert_eq!(rows[0].size, 0);
        assert!(events
            .iter()
            .any(|(phase, current, total, name, path)| phase == "local"
                && *current == 0
                && *total == 1
                && name == "demo mod"
                && path == &package));
        assert_eq!(events.last().unwrap().1, 1);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn startup_progress_keeps_persisted_cache_across_restarts() {
        let root = std::env::temp_dir().join(format!("ets2-progress-cache-{}", now_ms()));
        let package = root.join("mod/demo_mod");
        fs::create_dir_all(&package).unwrap();
        fs::write(
            package.join("manifest.sii"),
            "SiiNunit\n{\nmod_package : .demo {\n display_name: \"Demo Mod\"\n}\n}\n",
        )
        .unwrap();
        let paths = Paths {
            game_root: root.clone(),
            mod_root: root.join("mod"),
            profiles_root: root.join("profiles"),
            steam_profiles_root: None,
            cloud_profiles_root: None,
            workshop_roots: Vec::new(),
            game_executable: None,
        };
        let database = root.join("index.db");
        let mut events = Vec::new();
        let mut run = || {
            events.clear();
            let summary = scan_mod_inputs_with_progress(
                paths.clone(),
                database.clone(),
                Arc::new(AtomicBool::new(false)),
                &mut |phase, current, total, name, path| {
                    events.push((
                        phase.to_string(),
                        current,
                        total,
                        name.to_string(),
                        path.to_path_buf(),
                    ));
                },
            )
            .unwrap();
            (summary, events.clone())
        };
        let (first, events) = run();
        assert_eq!(first.added, 1);
        assert!(events.iter().any(|event| event.0 == "metadata"
            && event.1 == 0
            && event.2 == 1
            && event.4 == fs::canonicalize(&package).unwrap()));
        assert_eq!(events.last().unwrap().0, "complete");
        let db = open_db(&database).unwrap();
        let timestamp: i64 = db
            .query_row("SELECT scanned_at_ms FROM mod_package_v2", [], |row| {
                row.get(0)
            })
            .unwrap();
        drop(db);
        let (second, events) = run();
        assert_eq!(
            (
                second.added,
                second.updated,
                second.removed,
                second.inspected
            ),
            (0, 0, 0, 0)
        );
        assert!(!events.iter().any(|event| event.0 == "metadata"));
        assert!(events
            .iter()
            .any(|event| event.0 == "cached" && event.1 == 1 && event.3 == "Demo Mod"));
        let db = open_db(&database).unwrap();
        let next_timestamp: i64 = db
            .query_row("SELECT scanned_at_ms FROM mod_package_v2", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(timestamp, next_timestamp);
        drop(db);
        fs::write(package.join("added.txt"), "new file").unwrap();
        let (changed, events) = run();
        assert_eq!(changed.updated, 0);
        assert_eq!(changed.inspected, 0);
        assert!(!events.iter().any(|event| event.0 == "metadata"));
        fs::remove_dir_all(&package).unwrap();
        let (removed, events) = run();
        assert_eq!((removed.total, removed.removed), (0, 1));
        assert_eq!(events.last().unwrap().0, "complete");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn startup_ignores_legacy_directory_header_signature() {
        let root = std::env::temp_dir().join(format!("ets2-legacy-header-{}", now_ms()));
        let package = root.join("mod/demo_mod");
        fs::create_dir_all(&package).unwrap();
        fs::write(
            package.join("manifest.sii"),
            "SiiNunit\n{\nmod_package : .demo {\n display_name: \"Demo Mod\"\n}\n}\n",
        )
        .unwrap();
        let paths = Paths {
            game_root: root.clone(),
            mod_root: root.join("mod"),
            profiles_root: root.join("profiles"),
            steam_profiles_root: None,
            cloud_profiles_root: None,
            workshop_roots: Vec::new(),
            game_executable: None,
        };
        let database = root.join("index.db");
        let cancelled = Arc::new(AtomicBool::new(false));
        scan_mod_inputs_with_progress(
            paths.clone(),
            database.clone(),
            Arc::clone(&cancelled),
            &mut |_, _, _, _, _| {},
        )
        .unwrap();
        {
            let connection = open_db(&database).unwrap();
            connection
                .execute(
                    "UPDATE mod_package_header_state
                     SET size = 0, modified_ms = 1, is_directory = 1",
                    [],
                )
                .unwrap();
        }
        let mut events = Vec::new();
        let summary = startup_incremental_scan(
            paths,
            database.clone(),
            cancelled,
            &mut |phase, current, total, name, path| {
                events.push((
                    phase.to_string(),
                    current,
                    total,
                    name.to_string(),
                    path.to_path_buf(),
                ));
            },
        )
        .unwrap();
        assert_eq!(summary.inspected, 0);
        assert_eq!(summary.added, 0);
        assert_eq!(summary.updated, 0);
        assert!(!events.iter().any(|event| event.0 == "metadata"));
        assert!(events.iter().any(|event| event.0 == "cached"));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn header_state_backfill_uses_persisted_mod_signature() {
        let connection = Connection::open_in_memory().unwrap();
        open_db_schema(&connection).unwrap();
        ensure_header_state_table(&connection).unwrap();
        connection
            .execute(
                "INSERT INTO mod_package_v2(path,mod_id,package_name,package_type,display_name,author,version,size,modified_ms,fingerprint,scanned_at_ms)
                 VALUES('x','x','x','scs','X','','',42,99,123,1)",
                [],
            )
            .unwrap();
        backfill_mod_header_state(&connection).unwrap();
        let row = connection
            .query_row(
                "SELECT size,modified_ms,is_directory FROM mod_package_header_state WHERE path='x'",
                [],
                |row| {
                    Ok((
                        row.get::<_, i64>(0)?,
                        row.get::<_, i64>(1)?,
                        row.get::<_, i64>(2)?,
                    ))
                },
            )
            .unwrap();
        assert_eq!(row, (42, 99, 0));
    }

    #[test]
    fn failed_index_write_does_not_report_completion() {
        let mut connection = Connection::open_in_memory().unwrap();
        open_db_schema(&connection).unwrap();
        connection
            .execute_batch(
                "CREATE TRIGGER reject_write BEFORE INSERT ON mod_package_v2
            BEGIN SELECT RAISE(ABORT, 'test write failure'); END;",
            )
            .unwrap();
        let row = ModDto {
            id: "test".into(),
            package_name: "test".into(),
            path: "missing-test.scs".into(),
            package_type: "scs".into(),
            display_name: "Test Mod".into(),
            author: String::new(),
            version: String::new(),
            size: 0,
            modified_ms: 0,
            fingerprint: 0,
            enabled: false,
            category: "unknown".into(),
        };
        let mut phases = Vec::new();
        let result = sync_index_with_progress(&mut connection, &[row], &mut |phase, _, _, _, _| {
            phases.push(phase.to_string())
        });
        assert!(result.is_err());
        assert!(phases.iter().any(|phase| phase == "metadata"));
        assert!(!phases.iter().any(|phase| phase == "complete"));
        assert!(load_cached(&connection).unwrap().is_empty());
    }

    #[test]
    fn numeric_workshop_names_are_refreshed_from_persisted_metadata() {
        let row = ModDto {
            id: "1061306287".into(),
            package_name: "mod_workshop_package.1061306287".into(),
            path: "C:\\workshop\\1061306287".into(),
            package_type: "workshop".into(),
            display_name: "1061306287".into(),
            author: String::new(),
            version: String::new(),
            size: 0,
            modified_ms: 0,
            enabled: false,
            category: "unknown".into(),
            fingerprint: 0,
        };
        assert!(metadata_needs_refresh(&row));
    }

    #[test]
    fn localization_parser_reads_key_value_arrays() {
        let entries = parse_localization_text(
            "SiiNunit\n{\n localization_db : .x {\n  key[]: \"city.demo\"\n  val[]: \"Demo City\"\n }\n}\n",
            "mod.scs::locale/en_us/localization.sii",
            "mod.scs",
            "city",
        );
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].key, "city.demo");
        assert_eq!(entries[0].value, "Demo City");
        assert_eq!(entries[0].status, "native");
    }

    #[test]
    fn localization_parser_reads_indexed_arrays_and_country_definitions() {
        let entries = parse_localization_text(
            "SiiNunit { localization_db : .x { key[0]: \"country.demo\" key[1]: \"country.empty\" val[0]: \"Demo Country\" } }",
            "base.scs::locale/zh_cn/localization.sii",
            "base.scs",
            "country",
        );
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].key, "country.demo");
        assert_eq!(entries[0].value, "Demo Country");
        assert_eq!(entries[1].key, "country.empty");
        assert!(entries[1].value.is_empty());

        let definitions = parse_definition_text(
            "SiiNunit { country_data : country.demo { country_name: \"Demo\" country_name_localized: \"@@country.demo@@\" } }",
            "base.scs::def/world/country.sii",
            "base.scs",
            "country",
        );
        assert_eq!(definitions.len(), 1);
        assert_eq!(definitions[0].key, "country.demo");
        assert_eq!(definitions[0].source_name, "Demo");
    }

    #[test]
    fn promods_definition_and_locale_are_joined_by_key() {
        let definition = parse_definition_text(
            r#"SiiNunit {
                city_data : city.promods_demo {
                    city_name: "Promods Demo"
                    city_name_localized: "@@city.promods_demo@@"
                }
            }"#,
            "promods.scs::def/world/city.sii",
            "promods.scs",
            "city",
        );
        let locale = parse_localization_text(
            r#"SiiNunit {
                localization_db : .promods {
                    key[0]: "city.promods_demo"
                    key[1]: "city.promods_indexed"
                    val[1]: "Indexed City"
                    val[0]: "Promods 示例城市"
                }
            }"#,
            "promods.scs::locale/zh_cn/city.sii",
            "promods.scs",
            "unknown",
        );
        let merged = merge_localization_entries(vec![locale, definition]);
        assert_eq!(merged.len(), 2);
        let demo = merged
            .iter()
            .find(|entry| entry.key == "city.promods_demo")
            .unwrap();
        assert_eq!(demo.source_name, "Promods Demo");
        assert_eq!(demo.value, "Promods 示例城市");
        assert_eq!(demo.category, "city");
        let indexed = merged
            .iter()
            .find(|entry| entry.key == "city.promods_indexed")
            .unwrap();
        assert_eq!(indexed.value, "Indexed City");
        assert_eq!(indexed.category, "city");
    }

    #[test]
    fn localization_categories_include_tips() {
        assert_eq!(category_for_path("locale/zh_cn/tips.sii"), "tips");
        assert_eq!(category_for_path("locale/zh_cn/hints.sui"), "tips");
        assert_eq!(category_for_path("def/world/city.sii"), "city");
        assert_eq!(category_for_path("def/world/country.sii"), "country");
        assert_eq!(category_for_path("def/world/ferry.sii"), "ferry");
    }

    #[test]
    fn localization_parser_does_not_cross_pair_multiple_units() {
        let entries = parse_localization_text(
            r#"SiiNunit {
                localization_db : .first {
                    key[]: "city.first"
                    val[]: "First"
                }
                localization_db : .second {
                    key[]: "city.second"
                    val[]: "Second"
                }
            }"#,
            "base.scs::locale/zh_cn/localization.sii",
            "base.scs",
            "city",
        );
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].key, "city.first");
        assert_eq!(entries[0].value, "First");
        assert_eq!(entries[1].key, "city.second");
        assert_eq!(entries[1].value, "Second");
    }

    #[test]
    fn localization_snapshot_survives_reload() {
        let database = std::env::temp_dir().join(format!("ets2-l10n-cache-{}.db", now_ms()));
        let package_path = "base.scs".to_string();
        let fingerprint = ("scs-l10n-v5".to_string(), 123, 456);
        let entry = LocalizationEntryDto {
            key: "city.demo".into(),
            value: "示例城市".into(),
            source_name: "Demo City".into(),
            source_path: "base.scs::locale/zh_cn/city.sii".into(),
            package_name: "base.scs".into(),
            category: "city".into(),
            status: "native".into(),
            locale_key_present: true,
            def_locale_key_present: true,
            unit_name: String::new(),
            locale_key: "city.demo".into(),
        };
        {
            let mut connection = open_db(&database).expect("open cache db");
            save_localization_snapshots(
                &mut connection,
                "zh_cn",
                std::slice::from_ref(&package_path),
                &[(
                    package_path.clone(),
                    fingerprint.clone(),
                    vec![entry.clone()],
                )],
            )
            .expect("persist localization snapshot");
        }
        let connection = open_db(&database).expect("reopen cache db");
        let loaded = load_localization_snapshot(&connection, &package_path, "zh_cn", &fingerprint)
            .expect("load localization snapshot")
            .expect("snapshot exists");
        assert_eq!(loaded.len(), 1);
        assert_eq!(loaded[0].key, entry.key);
        assert_eq!(loaded[0].value, entry.value);
        assert_eq!(loaded[0].source_name, entry.source_name);
        let _ = fs::remove_file(database);
    }

    #[test]
    fn localization_incremental_commits_keep_previous_packages() {
        let database = std::env::temp_dir().join(format!("ets2-l10n-incremental-{}.db", now_ms()));
        let first_path = "base.scs".to_string();
        let second_path = "dlc_east.scs".to_string();
        let entry = |package: &str, key: &str| LocalizationEntryDto {
            key: key.into(),
            value: format!("value-{key}"),
            source_name: key.into(),
            source_path: format!("{package}::locale/zh_cn/city.sii"),
            package_name: package.into(),
            category: "city".into(),
            status: "native".into(),
            locale_key_present: true,
            def_locale_key_present: true,
            unit_name: String::new(),
            locale_key: key.into(),
        };
        {
            let mut connection = open_db(&database).expect("open cache db");
            let first = (
                first_path.clone(),
                ("scs-l10n-v5".into(), 1, 11),
                vec![entry("base.scs", "city.first")],
            );
            let second = (
                second_path.clone(),
                ("scs-l10n-v5".into(), 2, 22),
                vec![entry("dlc_east.scs", "city.second")],
            );
            save_localization_snapshot(&mut connection, "zh_cn", &first)
                .expect("persist first package");
            save_localization_snapshot(&mut connection, "zh_cn", &second)
                .expect("persist second package");
            let count: i64 = connection
                .query_row(
                    "SELECT COUNT(*) FROM localization_package_v2 WHERE target_locale = 'zh_cn'",
                    [],
                    |row| row.get(0),
                )
                .expect("count snapshots");
            assert_eq!(count, 2);
        }
        let connection = open_db(&database).expect("reopen cache db");
        assert!(load_localization_snapshot(
            &connection,
            &first_path,
            "zh_cn",
            &("scs-l10n-v5".into(), 1, 11),
        )
        .expect("load first package")
        .is_some());
        assert!(load_localization_snapshot(
            &connection,
            &second_path,
            "zh_cn",
            &("scs-l10n-v5".into(), 2, 22),
        )
        .expect("load second package")
        .is_some());
        let _ = fs::remove_file(database);
    }

    #[test]
    fn localization_merge_keeps_highest_priority_definition() {
        let high = LocalizationEntryDto {
            key: "city.demo".into(),
            value: String::new(),
            source_name: "High City".into(),
            source_path: "high".into(),
            package_name: "high".into(),
            category: "city".into(),
            status: "missing_value".into(),
            locale_key_present: true,
            def_locale_key_present: true,
            unit_name: String::new(),
            locale_key: "city.demo".into(),
        };
        let low = LocalizationEntryDto {
            key: "city.demo".into(),
            value: "低优先级翻译".into(),
            source_name: "Low City".into(),
            source_path: "low".into(),
            package_name: "low".into(),
            category: "city".into(),
            status: "native".into(),
            locale_key_present: true,
            def_locale_key_present: true,
            unit_name: String::new(),
            locale_key: "city.demo".into(),
        };
        let merged = merge_localization_entries(vec![vec![low], vec![high]]);
        assert_eq!(merged.len(), 1);
        assert!(merged[0].value.is_empty());
        assert_eq!(merged[0].package_name, "high");
    }

    #[test]
    fn localization_packages_follow_profile_ui_priority() {
        let mods = vec![
            ModDto {
                id: "low".into(),
                package_name: "low".into(),
                path: "low.scs".into(),
                package_type: "scs".into(),
                display_name: "Low".into(),
                author: String::new(),
                version: String::new(),
                size: 1,
                modified_ms: 1,
                enabled: true,
                category: "map".into(),
                fingerprint: 1,
            },
            ModDto {
                id: "high".into(),
                package_name: "high".into(),
                path: "high.scs".into(),
                package_type: "scs".into(),
                display_name: "High".into(),
                author: String::new(),
                version: String::new(),
                size: 1,
                modified_ms: 1,
                enabled: true,
                category: "map".into(),
                fingerprint: 1,
            },
        ];
        // Profile order is low -> high; UI and localization merge use its reverse.
        let ordered = order_localization_packages(mods, &["low".into(), "high".into()]);
        assert_eq!(
            ordered
                .into_iter()
                .map(|row| row.package_name)
                .collect::<Vec<_>>(),
            ["high", "low"]
        );
    }

    #[test]
    fn local_save_order_puts_autosave_after_named_saves() {
        let mut rows = vec![
            (1u8, "自动保存".to_string()),
            (0u8, "Zeta".to_string()),
            (0u8, "Alpha".to_string()),
            (2u8, "autosave2".to_string()),
        ];
        rows.sort_by(|a, b| a.0.cmp(&b.0).then_with(|| a.1.cmp(&b.1)));
        assert_eq!(
            rows.into_iter().map(|row| row.1).collect::<Vec<_>>(),
            ["Alpha", "Zeta", "自动保存", "autosave2"]
        );
    }

    #[test]
    fn local_save_listing_reads_info_names_and_excludes_readonly_profiles() {
        let root = std::env::temp_dir().join(format!("ets2modmanager-saves-{}", now_ms()));
        let save_root = root.join("save");
        fs::create_dir_all(&save_root).expect("create save directory");
        for (slot_id, display_name) in [
            ("1", "Zulu Save"),
            ("2", "Alpha Save"),
            ("autosave_drive", "Autosave Drive"),
            ("autosave", "Autosave"),
        ] {
            let slot = save_root.join(slot_id);
            fs::create_dir_all(&slot).expect("create slot");
            fs::write(slot.join("game.sii"), b"game-save").expect("write game");
            let info = format!(
                "SiiNunit\n{{\n save_container : .save {{\n  name : \"{}\"\n }}\n}}\n",
                escape_sii(display_name)
            );
            fs::write(
                slot.join("info.sii"),
                encode_scsc(info.as_bytes()).expect("encode info"),
            )
            .expect("write info");
        }
        let profile = ProfileDto {
            id: "local:demo".into(),
            name: "Demo".into(),
            company: String::new(),
            location: "local".into(),
            folder: normalize_path(&root),
            mod_count: 0,
            writable: true,
        };
        let slots = list_local_saves(&profile);
        assert_eq!(
            slots
                .iter()
                .map(|slot| slot.display_name.as_str())
                .collect::<Vec<_>>(),
            ["Alpha Save", "Zulu Save", "Autosave", "Autosave Drive"]
        );
        assert_eq!(
            slots
                .iter()
                .map(|slot| slot.slot_id.as_str())
                .collect::<Vec<_>>(),
            ["2", "1", "autosave", "autosave_drive"]
        );

        let readonly = ProfileDto {
            writable: false,
            location: "cloud".into(),
            ..profile
        };
        assert!(list_local_saves(&readonly).is_empty());
        let _ = fs::remove_dir_all(&root);
    }
}

#[cfg(test)]
fn open_db_schema(connection: &Connection) -> Result<(), String> {
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS mod_package_v2 (
               path TEXT PRIMARY KEY,
               mod_id TEXT NOT NULL,
               package_name TEXT NOT NULL,
               package_type TEXT NOT NULL,
               display_name TEXT NOT NULL,
               author TEXT NOT NULL DEFAULT '',
               version TEXT NOT NULL DEFAULT '',
               size INTEGER NOT NULL,
               modified_ms INTEGER NOT NULL,
               fingerprint INTEGER NOT NULL DEFAULT 0,
               scanned_at_ms INTEGER NOT NULL
             );
             CREATE TABLE IF NOT EXISTS preset (
               profile_id TEXT NOT NULL,
               name TEXT NOT NULL,
               active_mods_json TEXT NOT NULL,
               PRIMARY KEY(profile_id, name)
             );
             CREATE TABLE IF NOT EXISTS localization_package_v2 (
               package_path TEXT NOT NULL,
               target_locale TEXT NOT NULL,
               package_type TEXT NOT NULL,
               file_size INTEGER NOT NULL,
               modified_ms INTEGER NOT NULL,
               scanned_at_ms INTEGER NOT NULL,
               PRIMARY KEY(package_path, target_locale)
             );
             CREATE TABLE IF NOT EXISTS localization_entry_v2 (
               package_path TEXT NOT NULL,
               target_locale TEXT NOT NULL,
               entry_order INTEGER NOT NULL,
               key TEXT NOT NULL,
               value TEXT NOT NULL,
               source_name TEXT NOT NULL DEFAULT '',
               source_path TEXT NOT NULL,
               package_name TEXT NOT NULL,
               category TEXT NOT NULL,
               status TEXT NOT NULL,
               locale_key_present INTEGER NOT NULL,
               def_locale_key_present INTEGER NOT NULL,
               unit_name TEXT NOT NULL,
               locale_key TEXT NOT NULL,
               PRIMARY KEY(package_path, target_locale, entry_order)
             );
             CREATE INDEX IF NOT EXISTS ix_localization_entry_v2_key
               ON localization_entry_v2(target_locale, key);",
        )
        .map_err(|e| format!("initialize database failed: {e}"))?;
    ensure_media_cache_table(connection)
        .and_then(|_| ensure_localization_source_name_column(connection))
}

#[cfg(feature = "desktop")]
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let paths = Paths::detect();
    let database_path = paths.game_root.join("ets2modmanager.db");
    tauri::Builder::default()
        .manage(BackendState {
            inner: Arc::new(Mutex::new(Backend {
                paths,
                database_path,
                scan_cancelled: Arc::new(AtomicBool::new(false)),
                localization_cancelled: Arc::new(AtomicBool::new(false)),
            })),
        })
        .invoke_handler(tauri::generate_handler![
            profile_list,
            profile_read_active,
            profile_write_active,
            mod_list,
            mod_media,
            mod_media_batch,
            mod_scan,
            mod_initialize,
            mod_cancel,
            mod_directory_status,
            mod_directory_pick,
            mod_directory_change,
            category_list,
            category_mutate,
            mod_set_enabled,
            mod_move,
            mod_open_location,
            profile_open_location,
            preset_list,
            preset_save,
            preset_load,
            preset_delete,
            save_list_local,
            game_launch,
            localization_scan,
            localization_write_base,
            localization_save_as,
            localization_cancel,
            localization_base_get,
            localization_base_pick,
            crash_discover,
            crash_precheck,
            save_inspect_bsii,
            save_read_snapshot,
            save_read_inventory,
            save_mutate,
            save_mutate_object,
            check_update,
            download_update,
            install_update
        ])
        .setup(|_app| {
            // Remove any installer left over from a previous silent update.
            cleanup_update_downloads();
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running ETS2 Mod Manager");
}
