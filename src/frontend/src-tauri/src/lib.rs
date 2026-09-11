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
use std::{
    collections::{HashMap, HashSet},
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex, OnceLock,
    },
    time::{SystemTime, UNIX_EPOCH},
};
#[cfg(feature = "desktop")]
use tauri::State;

#[cfg(not(feature = "desktop"))]
type State<'a, T> = &'a T;
use zip::ZipArchive;

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

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SaveMutationRequest {
    path: String,
    operation: String,
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
        let Some(path) = cache_directory().map(|dir| dir.join("workshop_titles.json")) else {
            return HashMap::new();
        };
        let Ok(text) = fs::read_to_string(path) else {
            return HashMap::new();
        };
        serde_json::from_str::<HashMap<String, WorkshopCacheEntry>>(&text).unwrap_or_default()
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

fn workshop_cached_preview_url(workshop_id: &str) -> Option<String> {
    workshop_cache_entries()
        .get(workshop_id)
        .map(|entry| entry.preview_url.trim().to_string())
        .filter(|value| !value.is_empty())
}

fn legacy_icon_cache_path(mod_id: &str, package_path: &Path) -> Option<(PathBuf, String)> {
    let cache = cache_directory()?.join("mod_icons");
    let modified = fs::metadata(package_path)
        .ok()
        .and_then(|metadata| metadata.modified().ok())
        .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
        .map(|value| value.as_secs_f64())?;
    let mut hasher = Sha1::new();
    hasher.update(format!("{mod_id}\0{modified:.6}").as_bytes());
    let stem = format!("{:x}", hasher.finalize());
    for extension in ["jpg", "jpeg", "png", "webp", "bmp", "gif"] {
        let candidate = cache.join(format!("{stem}.{extension}"));
        if candidate.is_file() {
            return Some((candidate, extension.to_string()));
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
    let mut out = String::with_capacity(value.len());
    let mut chars = value.chars();
    while let Some(ch) = chars.next() {
        if ch == '\\' {
            match chars.next() {
                Some('"') => out.push('"'),
                Some('\\') => out.push('\\'),
                Some(other) => {
                    out.push('\\');
                    out.push(other);
                }
                None => out.push('\\'),
            }
        } else {
            out.push(ch);
        }
    }
    out
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
        result.push(ProfileDto {
            id: format!("{location}:{folder_id}"),
            name: parse_field(&text, "profile_name").unwrap_or_else(|| "Unnamed profile".into()),
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
    let mut result = Vec::new();
    result.extend(list_profiles_from(Some(&paths.profiles_root), "local"));
    result.extend(list_profiles_from(
        paths.steam_profiles_root.as_deref(),
        "steam",
    ));
    result.extend(list_profiles_from(
        paths.cloud_profiles_root.as_deref(),
        "cloud",
    ));
    result
}

fn open_db(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create database directory failed: {e}"))?;
    }
    let connection = Connection::open(path).map_err(|e| format!("open database failed: {e}"))?;
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
    ensure_mod_fingerprint_column(&connection)?;
    Ok(connection)
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

fn manifest_for(path: &Path) -> (String, String, String, String, String) {
    let mut manifest = archive_core::read_manifest(path).unwrap_or_default();
    if manifest.package_name.is_empty()
        && manifest.display_name.is_empty()
        && manifest.author.is_empty()
        && manifest.version.is_empty()
        && path.is_file()
    {
        if let Some(text) = read_zip_entry_text(path, "manifest.sii") {
            manifest = archive_core::parse_manifest(&text);
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

const MAX_MEDIA_BYTES: u64 = 8 * 1024 * 1024;

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

fn read_zip_entry_bytes(path: &Path, wanted: &str) -> Option<Vec<u8>> {
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

fn directory_image_path(root: &Path, icon_filename: &str) -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if !icon_filename.trim().is_empty() {
        candidates.push(root.join(icon_filename.replace('\\', "/")));
        candidates.push(root.join(Path::new(icon_filename).file_name()?));
    }
    for name in [
        "mod_icon.jpg",
        "icon.jpg",
        "preview.jpg",
        "thumbnail.jpg",
        "mod_icon.png",
        "icon.png",
        "preview.png",
        "thumbnail.png",
        "cover.jpg",
        "cover.png",
        "logo.jpg",
        "logo.png",
        "banner.jpg",
        "banner.png",
    ] {
        candidates.push(root.join(name));
    }
    for candidate in candidates {
        if candidate.is_file()
            && media_extension(candidate.to_string_lossy().as_ref()).is_some()
            && fs::metadata(&candidate)
                .map(|metadata| metadata.len() <= MAX_MEDIA_BYTES)
                .unwrap_or(false)
        {
            return Some(candidate);
        }
    }
    let mut stack = vec![root.to_path_buf()];
    let mut inspected = 0usize;
    while let Some(current) = stack.pop() {
        if inspected >= 2000 {
            break;
        }
        let Ok(entries) = fs::read_dir(&current) else {
            continue;
        };
        for entry in entries.flatten() {
            inspected += 1;
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
                continue;
            }
            if media_extension(path.to_string_lossy().as_ref()).is_some()
                && fs::metadata(&path)
                    .map(|metadata| metadata.len() <= MAX_MEDIA_BYTES)
                    .unwrap_or(false)
            {
                return Some(path);
            }
        }
    }
    None
}

fn package_media_url(path: &Path, mod_id: &str, icon_filename: &str) -> Option<String> {
    if path.is_dir() {
        if let Some(image) = directory_image_path(path, icon_filename) {
            if let Ok(bytes) = fs::read(&image) {
                if let Some(url) = data_url(bytes, image.to_string_lossy().as_ref()) {
                    return Some(url);
                }
            }
        }
    } else if path.is_file() {
        let mut candidates = Vec::new();
        if !icon_filename.trim().is_empty() {
            candidates.push(icon_filename.to_string());
        }
        candidates.extend(
            [
                "mod_icon.jpg",
                "icon.jpg",
                "preview.jpg",
                "thumbnail.jpg",
                "mod_icon.png",
                "icon.png",
                "preview.png",
                "thumbnail.png",
                "cover.jpg",
                "cover.png",
                "logo.jpg",
                "logo.png",
                "banner.jpg",
                "banner.png",
            ]
            .into_iter()
            .map(str::to_string),
        );
        for candidate in candidates {
            if let Some(bytes) = read_zip_entry_bytes(path, &candidate) {
                if let Some(url) = data_url(bytes, &candidate) {
                    return Some(url);
                }
            }
        }
    }
    if let Some((cache_path, _)) = legacy_icon_cache_path(mod_id, path) {
        if let Ok(bytes) = fs::read(&cache_path) {
            return data_url(bytes, cache_path.to_string_lossy().as_ref());
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

fn resolve_mod_media(row: &ModDto) -> ModMediaDto {
    let workshop_id = row.id.trim().trim_end_matches("_workshop").to_string();
    let cached_preview = if is_workshop(row) {
        cached_workshop_preview_url(&workshop_id)
            .or_else(|| workshop_cached_preview_url(&workshop_id))
    } else {
        None
    };
    let cached_icon =
        legacy_icon_cache_path(&row.id, Path::new(&row.path)).and_then(|(path, _)| {
            fs::read(&path)
                .ok()
                .and_then(|bytes| data_url(bytes, path.to_string_lossy().as_ref()))
        });
    let package_url = cached_icon.or_else(|| {
        let icon_filename = manifest_for(Path::new(&row.path)).4;
        package_media_url(Path::new(&row.path), &row.id, &icon_filename)
    });
    ModMediaDto {
        mod_id: row.id.clone(),
        icon_url: package_url.clone().or_else(|| cached_preview.clone()),
        preview_url: cached_preview.or(package_url),
    }
}

fn directory_signature(path: &Path, cancelled: &AtomicBool) -> (u64, i64, u64) {
    let mut stack = vec![path.to_path_buf()];
    let mut total_size = 0u64;
    let mut latest_modified = 0i64;
    let mut fingerprint = 1469598103934665603u64;
    let mut files = Vec::new();
    while let Some(current) = stack.pop() {
        if cancelled.load(Ordering::Relaxed) {
            break;
        }
        let Ok(metadata) = fs::metadata(&current) else {
            continue;
        };
        if let Some(modified) = metadata
            .modified()
            .ok()
            .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
            .map(|value| value.as_millis() as i64)
        {
            latest_modified = latest_modified.max(modified);
        }
        if metadata.is_file() {
            total_size = total_size.saturating_add(metadata.len());
            let relative = current
                .strip_prefix(path)
                .unwrap_or(&current)
                .to_string_lossy()
                .replace('\\', "/");
            let modified = metadata
                .modified()
                .ok()
                .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
                .map(|value| value.as_millis() as u64)
                .unwrap_or_default();
            files.push((relative, metadata.len(), modified));
            continue;
        }
        let Ok(entries) = fs::read_dir(&current) else {
            continue;
        };
        for entry in entries.flatten() {
            stack.push(entry.path());
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

fn discover_packages(root: Option<&Path>, workshop: bool, cancelled: &AtomicBool) -> Vec<ModDto> {
    let Some(root) = root.filter(|p| p.is_dir()) else {
        return Vec::new();
    };
    let Ok(entries) = fs::read_dir(root) else {
        return Vec::new();
    };
    let mut result = Vec::new();
    for entry in entries.flatten() {
        if cancelled.load(Ordering::Relaxed) {
            break;
        }
        let path = entry.path();
        let is_dir = path.is_dir();
        let extension = path
            .extension()
            .and_then(|x| x.to_str())
            .unwrap_or_default()
            .to_ascii_lowercase();
        if !is_dir && extension != "scs" && extension != "zip" {
            continue;
        }
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
        let metadata = fs::metadata(&path).ok();
        let (size, modified_ms, fingerprint) = if is_dir {
            directory_signature(&path, cancelled)
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
        let display_name = if workshop {
            workshop_cached_title(&id).unwrap_or_else(|| id.replace('_', " "))
        } else {
            id.replace('_', " ")
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
    result
}

fn discover_workshop_packages(roots: &[PathBuf], cancelled: &AtomicBool) -> Vec<ModDto> {
    let mut result = Vec::new();
    for root in roots {
        result.extend(discover_packages(Some(root), true, cancelled));
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
            Ok(ModDto {
                id: row.get(0)?,
                package_name: row.get(1)?,
                path: row.get(2)?,
                package_type: row.get(3)?,
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

fn metadata_needs_refresh(row: &ModDto) -> bool {
    let display = row.display_name.trim();
    let package = row.package_name.trim();
    display.is_empty()
        || display == row.id.trim()
        || display.chars().all(|value| value.is_ascii_digit())
        || package.is_empty()
        || package.starts_with('.')
}

fn sync_index(connection: &mut Connection, incoming: &[ModDto]) -> Result<ScanSummary, String> {
    let started = std::time::Instant::now();
    let cached = load_cached(connection)?;
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
    }
    for mod_row in incoming {
        let changed = old
            .get(&mod_row.path)
            .map(|(modified, size, fingerprint)| {
                *modified != mod_row.modified_ms
                    || *size != mod_row.size
                    || *fingerprint != mod_row.fingerprint
            })
            .unwrap_or(true);
        let previous = cached.iter().find(|row| row.path == mod_row.path);
        if !changed && !previous.is_some_and(metadata_needs_refresh) {
            continue;
        }
        if changed && old.contains_key(&mod_row.path) {
            updated += 1;
        } else if !old.contains_key(&mod_row.path) {
            added += 1;
        }
        let mut enriched = mod_row.clone();
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
    }
    tx.commit()
        .map_err(|e| format!("commit mod index failed: {e}"))?;
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
        let mut stack = vec![path.to_path_buf()];
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
    (kind, size, modified)
}

fn is_localization_path(path: &str) -> bool {
    let normalized = path.replace('\\', "/").to_ascii_lowercase();
    (normalized.ends_with(".sii") || normalized.ends_with(".sui"))
        && (normalized.contains("/locale/")
            || normalized.starts_with("locale/")
            || normalized.contains("localization")
            || normalized.contains("translation")
            || normalized.contains("language")
            || normalized.contains("/local/"))
}

fn is_definition_path(path: &str) -> bool {
    let normalized = path.replace('\\', "/").to_ascii_lowercase();
    (normalized.ends_with(".sii") || normalized.ends_with(".sui"))
        && (normalized.starts_with("def/")
            || normalized.contains("/def/")
            || normalized.contains("/city/")
            || normalized.contains("/country/")
            || normalized.contains("/ferry/"))
}

fn category_for_path(path: &str) -> String {
    let value = path.to_ascii_lowercase();
    if value.contains("country") {
        "country".into()
    } else if value.contains("ferry") {
        "ferry".into()
    } else if value.contains("city") {
        "city".into()
    } else {
        "unknown".into()
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
    let mut keys = Vec::new();
    let mut values = Vec::new();
    let mut scalar = Vec::new();
    for line in text.lines() {
        let trimmed = line.trim();
        let Some((raw_key, raw_value)) = trimmed.split_once(':') else {
            continue;
        };
        let key = raw_key.trim();
        let value = quoted_value(raw_value);
        if key.eq_ignore_ascii_case("key[]") {
            if !value.is_empty() {
                keys.push(value);
            }
        } else if key.eq_ignore_ascii_case("val[]") {
            values.push(value);
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
    for (index, key) in keys.into_iter().enumerate() {
        let value = values.get(index).cloned().unwrap_or_default();
        output.push(LocalizationEntryDto {
            key: key.clone(),
            value: value.clone(),
            source_path: source_path.to_string(),
            package_name: package_name.to_string(),
            category: category.to_string(),
            status: if value.is_empty() {
                "missing_value".into()
            } else {
                "native".into()
            },
            locale_key_present: true,
            def_locale_key_present: true,
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
        output.push(LocalizationEntryDto {
            key: key.clone(),
            value: value.clone(),
            source_path: source_path.to_string(),
            package_name: package_name.to_string(),
            category: category.to_string(),
            status: if value.is_empty() {
                "missing_value".into()
            } else {
                "native".into()
            },
            locale_key_present: true,
            def_locale_key_present: true,
            unit_name: String::new(),
            locale_key: key,
        });
    }
    output
}

fn parse_definition_text(
    text: &str,
    source_path: &str,
    package_name: &str,
    category: &str,
) -> Vec<LocalizationEntryDto> {
    let block =
        Regex::new(r"(?is)(city_data|country_data|ferry_data)\s*:\s*([A-Za-z0-9_.-]+)\s*\{(.*?)\}")
            .expect("definition regex");
    let field = Regex::new(
        r#"(?m)(city_name|city_name_localized|name|name_localized|ferry_name|ferry_name_localized)\s*:\s*"((?:\\.|[^"\\])*)""#,
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
            "ferry_data" => "ferry_name",
            _ => "name",
        };
        let localized_field = match type_name.to_ascii_lowercase().as_str() {
            "city_data" => "city_name_localized",
            "ferry_data" => "ferry_name_localized",
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
    package_name: &str,
    cancelled: &AtomicBool,
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
        if !is_localization_path(&relative) && !is_definition_path(&relative) {
            continue;
        }
        let Ok(text) = fs::read_to_string(&path) else {
            continue;
        };
        let source_path = format!("{}::{}", root.display(), relative);
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
        let locale_match = normalized
            .to_ascii_lowercase()
            .contains(&format!("locale/{}/", locale.to_ascii_lowercase()));
        if (!locale_match && !is_definition_path(&normalized))
            || (!is_localization_path(&normalized) && !is_definition_path(&normalized))
        {
            continue;
        }
        let mut bytes = Vec::new();
        if entry.read_to_end(&mut bytes).is_err() {
            continue;
        }
        let text = String::from_utf8_lossy(&bytes);
        let source_path = format!("{}::{}", path.display(), normalized);
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

fn scan_localization_package(
    path: &Path,
    locale: &str,
    cancelled: &AtomicBool,
) -> Vec<LocalizationEntryDto> {
    if path.is_dir() {
        return scan_localization_directory(
            path,
            path.file_name()
                .and_then(|value| value.to_str())
                .unwrap_or_default(),
            cancelled,
        );
    }
    scan_localization_archive(path, locale, cancelled)
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
            "SELECT key, value, source_path, package_name, category, status,
                    locale_key_present, def_locale_key_present, unit_name, locale_key
             FROM localization_entry_v2
             WHERE package_path = ?1 AND target_locale = ?2
             ORDER BY entry_order",
        )
        .map_err(|error| format!("prepare localization snapshot failed: {error}"))?;
    let rows = statement
        .query_map(params![path, locale], |row| {
            Ok(LocalizationEntryDto {
                key: row.get(0)?,
                value: row.get(1)?,
                source_path: row.get(2)?,
                package_name: row.get(3)?,
                category: row.get(4)?,
                status: row.get(5)?,
                locale_key_present: row.get::<_, i64>(6)? != 0,
                def_locale_key_present: row.get::<_, i64>(7)? != 0,
                unit_name: row.get(8)?,
                locale_key: row.get(9)?,
            })
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
                     (package_path, target_locale, entry_order, key, value, source_path,
                      package_name, category, status, locale_key_present,
                      def_locale_key_present, unit_name, locale_key)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
                    params![
                        path,
                        locale,
                        index as i64,
                        entry.key,
                        entry.value,
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
            let path =
                row.map_err(|error| format!("read stale localization snapshot failed: {error}"))?;
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
    transaction
        .commit()
        .map_err(|error| format!("commit localization snapshots failed: {error}"))?;
    Ok(())
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
                if result[index].value.is_empty() && !entry.value.is_empty() {
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
    if mods.is_empty() {
        let mut discovered = discover_packages(Some(&paths.mod_root), false, cancelled);
        discovered.extend(discover_workshop_packages(&paths.workshop_roots, cancelled));
        if !discovered.is_empty() {
            sync_index(connection, &discovered)?;
            mods = dedupe_mods(load_cached(connection)?);
        }
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
            .position(|row| rows_match(&row.package_name, &package))
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
) -> Result<LocalizationScanDto, String> {
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
    let mut connection = open_db(&database_path)?;
    let packages = local_packages_for_profile(&paths, &mut connection, &profile, &cancelled)?;
    let mut snapshots = Vec::new();
    let mut all_entries = Vec::new();
    let mut inspected = 0usize;
    let mut cached = 0usize;
    for package in &packages {
        if cancelled.load(Ordering::Relaxed) {
            return Err("Localization scan cancelled.".into());
        }
        let fingerprint = package_fingerprint(Path::new(&package.path));
        if let Some(entries) =
            load_localization_snapshot(&connection, &package.path, &locale, &fingerprint)?
        {
            cached += 1;
            all_entries.push(entries);
            continue;
        }
        inspected += 1;
        let entries = scan_localization_package(Path::new(&package.path), &locale, &cancelled);
        if cancelled.load(Ordering::Relaxed) {
            return Err("Localization scan cancelled.".into());
        }
        snapshots.push((package.path.clone(), fingerprint, entries.clone()));
        all_entries.push(entries);
    }
    if cancelled.load(Ordering::Relaxed) {
        return Err("Localization scan cancelled.".into());
    }
    let current_paths = packages
        .iter()
        .map(|package| package.path.clone())
        .collect::<Vec<_>>();
    save_localization_snapshots(&mut connection, &locale, &current_paths, &snapshots)?;
    let entries = merge_localization_entries(all_entries);
    Ok(LocalizationScanDto {
        packages: packages.len(),
        inspected,
        cached,
        elapsed_ms: started.elapsed().as_millis(),
        entries,
    })
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
async fn localization_scan(
    request: LocalizationScanRequest,
    state: State<'_, BackendState>,
) -> Result<LocalizationScanDto, String> {
    let (paths, database_path, cancelled) = localization_scan_inputs(&state)?;
    cancelled.store(false, Ordering::Relaxed);

    #[cfg(feature = "desktop")]
    {
        return tauri::async_runtime::spawn_blocking(move || {
            localization_scan_impl(request, paths, database_path, cancelled)
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
    let mut latest = None;
    for (source, root) in candidates {
        let crash = root.join("game.crash.txt");
        if !crash.is_file() {
            continue;
        }
        let modified = fs::metadata(&crash)
            .and_then(|value| value.modified())
            .ok()
            .unwrap_or(UNIX_EPOCH);
        if latest
            .as_ref()
            .is_none_or(|(_, current, _)| modified > *current)
        {
            latest = Some((source, modified, crash));
        }
    }
    if let Some((source, _, crash)) = latest {
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
    CrashPairDto {
        crash_path: None,
        log_path: None,
        source: None,
    }
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn crash_discover(state: State<'_, BackendState>) -> Result<CrashPairDto, String> {
    let backend = state
        .inner
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    Ok(crash_pair(&backend.paths))
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
    let backup = path.with_extension(format!("bak-{}", now_ms()));
    fs::copy(path, &backup).map_err(|error| format!("backup save failed: {error}"))?;
    if let Err(error) = atomic_write(path, &output) {
        let _ = fs::copy(&backup, path);
        return Err(error);
    }
    let verify = match save_snapshot(path) {
        Ok(snapshot) => snapshot,
        Err(verify_error) => {
            let restore_error = fs::copy(&backup, path).err();
            return Err(match restore_error {
                Some(error) => format!(
                    "Save write verification read failed: {verify_error}; restoring backup failed: {error}"
                ),
                None => format!(
                    "Save write verification read failed: {verify_error}; original save restored from backup."
                ),
            });
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
        let _ = fs::copy(&backup, path);
        return Err("Save write verification failed; original save restored.".into());
    }
    Ok(SaveMutationDto {
        success: true,
        operation: operation.into(),
        message: format!("Updated {}.", target_field.1),
        backup_path: Some(normalize_path(&backup)),
        value: Some(write_value),
    })
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn save_read_snapshot(request: BsiiInspectRequest) -> Result<SaveSnapshotDto, String> {
    save_snapshot(Path::new(&request.path))
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn save_mutate(request: SaveMutationRequest) -> Result<SaveMutationDto, String> {
    mutate_save(Path::new(&request.path), &request.operation, request.value)
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

fn apply_enabled(mods: &mut [ModDto], active: &[String]) {
    for row in mods {
        row.enabled = active
            .iter()
            .any(|entry| rows_match(&row.package_name, entry));
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
    let backup = path.with_extension(format!("bak-{}", now_ms()));
    fs::copy(&path, &backup).map_err(|e| format!("backup profile failed: {e}"))?;
    if let Err(write_error) = atomic_write(&path, &encoded) {
        let restore_error = fs::copy(&backup, &path).err();
        return Err(match restore_error {
            Some(error) => format!("{write_error}; restoring backup failed: {error}"),
            None => format!("{write_error}; original profile restored from backup."),
        });
    }
    let verify = match read_sii(&path) {
        Ok(value) => value,
        Err(read_error) => {
            let restore_error = fs::copy(&backup, &path).err();
            return Err(match restore_error {
                Some(error) => format!(
                    "profile write verification read failed: {read_error}; restoring backup failed: {error}"
                ),
                None => format!(
                    "profile write verification read failed: {read_error}; original profile restored from backup."
                ),
            });
        }
    };
    if parse_active_mods(&verify) != active_mods {
        let restore_error = fs::copy(&backup, &path).err();
        return Err(match restore_error {
            Some(error) => {
                format!("active_mods write verification failed; restoring backup failed: {error}")
            }
            None => "active_mods write verification failed; original profile restored from backup."
                .into(),
        });
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
    std::process::Command::new("tasklist")
        .args(["/FO", "CSV", "/NH"])
        .output()
        .map(|output| {
            let stdout = String::from_utf8_lossy(&output.stdout).to_ascii_lowercase();
            stdout.contains("\"eurotrucks2.exe\"") || stdout.contains("\"amtrucks.exe\"")
        })
        .unwrap_or(false)
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
    let db = open_db(&backend.database_path)?;
    let mut mods = dedupe_mods(load_cached(&db)?);
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
                ordered.push(row);
            } else {
                ordered.push(ModDto {
                    id: package.clone(),
                    package_name: package.clone(),
                    path: String::new(),
                    package_type: "unknown".into(),
                    display_name: package.clone(),
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
fn mod_media(request: ModMediaRequest) -> Result<ModMediaDto, String> {
    let row = ModDto {
        id: request.mod_id,
        package_name: String::new(),
        path: request.path,
        package_type: request.package_type,
        display_name: String::new(),
        author: String::new(),
        version: String::new(),
        size: 0,
        modified_ms: 0,
        enabled: false,
        category: String::new(),
        fingerprint: 0,
    };
    Ok(resolve_mod_media(&row))
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn mod_media_batch(requests: Vec<ModMediaRequest>) -> Result<Vec<ModMediaDto>, String> {
    Ok(requests
        .into_iter()
        .map(|request| {
            let row = ModDto {
                id: request.mod_id,
                package_name: String::new(),
                path: request.path,
                package_type: request.package_type,
                display_name: String::new(),
                author: String::new(),
                version: String::new(),
                size: 0,
                modified_ms: 0,
                enabled: false,
                category: String::new(),
                fingerprint: 0,
            };
            resolve_mod_media(&row)
        })
        .collect())
}

fn is_workshop(row: &ModDto) -> bool {
    row.package_type.eq_ignore_ascii_case("workshop")
        || row.path.to_ascii_lowercase().contains("workshop")
}

fn dedupe_mods(mods: Vec<ModDto>) -> Vec<ModDto> {
    let mut by_key: HashMap<String, ModDto> = HashMap::new();
    for row in mods {
        let key = canonical_package(&row.package_name);
        match by_key.get(&key) {
            Some(existing) if !is_workshop(existing) && is_workshop(&row) => {}
            Some(existing) if is_workshop(existing) && !is_workshop(&row) => {
                by_key.insert(key, row);
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
    cancelled.store(false, Ordering::Relaxed);
    let mut discovered = discover_packages(Some(&paths.mod_root), false, &cancelled);
    discovered.extend(discover_workshop_packages(
        &paths.workshop_roots,
        &cancelled,
    ));
    if cancelled.load(Ordering::Relaxed) {
        return Err("Scan cancelled.".into());
    }
    let mut db = open_db(&database_path)?;
    sync_index(&mut db, &discovered)
}

#[cfg_attr(feature = "desktop", tauri::command(rename_all = "camelCase"))]
fn mod_initialize(state: State<'_, BackendState>) -> Result<ScanSummary, String> {
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
    // Startup uses the same persisted index and incremental scanner as the
    // explicit Scan button. It therefore detects additions/removals while
    // keeping unchanged package metadata untouched.
    scan_mod_inputs(paths, database_path, cancelled)
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
    fn active_mod_parser_keeps_game_order() {
        let text = "profile : .profile {\n active_mods[0]: \"high\"\n active_mods[1]: \"low\"\n}";
        assert_eq!(parse_active_mods(text), ["high", "low"]);
        assert_eq!(
            profile_order_to_ui(&parse_active_mods(text)),
            ["low", "high"]
        );
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
    fn directory_fingerprint_detects_same_size_rename() {
        let root = std::env::temp_dir().join(format!("ets2modmanager-fingerprint-{}", now_ms()));
        fs::create_dir_all(&root).expect("create temp directory");
        let first = root.join("first.scs");
        let second = root.join("second.scs");
        fs::write(&first, b"same-size").expect("write first package");
        let before = directory_signature(&root, &AtomicBool::new(false));
        fs::rename(&first, &second).expect("rename package");
        let after = directory_signature(&root, &AtomicBool::new(false));
        let _ = fs::remove_dir_all(&root);
        assert_eq!(before.0, after.0);
        assert_ne!(before.2, after.2);
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
        let second_summary = sync_index(&mut connection, &[first.clone()]).expect("second sync");
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
    fn localization_merge_keeps_first_priority_value() {
        let high = LocalizationEntryDto {
            key: "city.demo".into(),
            value: String::new(),
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
            source_path: "low".into(),
            package_name: "low".into(),
            category: "city".into(),
            status: "native".into(),
            locale_key_present: true,
            def_locale_key_present: true,
            unit_name: String::new(),
            locale_key: "city.demo".into(),
        };
        let merged = merge_localization_entries(vec![vec![high], vec![low]]);
        assert_eq!(merged.len(), 1);
        assert_eq!(merged[0].value, "低优先级翻译");
        assert_eq!(merged[0].package_name, "low");
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
        .map_err(|e| format!("initialize database failed: {e}"))
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
            mod_set_enabled,
            mod_move,
            preset_list,
            preset_save,
            preset_load,
            preset_delete,
            save_list_local,
            game_launch,
            localization_scan,
            localization_cancel,
            crash_discover,
            crash_precheck,
            save_inspect_bsii,
            save_read_snapshot,
            save_mutate
        ])
        .run(tauri::generate_context!())
        .expect("error while running ETS2 Mod Manager");
}
