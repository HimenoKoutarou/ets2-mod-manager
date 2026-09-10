#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use aes::cipher::{BlockDecrypt, BlockEncrypt, KeyInit};
use aes::{cipher::generic_array::GenericArray, Aes256};
use flate2::{read::ZlibDecoder, write::ZlibEncoder, Compression};
use getrandom::fill as fill_random;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::{
    collections::{HashMap, HashSet},
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::{SystemTime, UNIX_EPOCH},
};
use tauri::State;

const SCSC_KEY: [u8; 32] = [
    0x2A, 0x5F, 0xCB, 0x17, 0x91, 0xD2, 0x2F, 0xB6, 0x02, 0x45, 0xB3, 0xD8, 0x36, 0x9E,
    0xD0, 0xB2, 0xC2, 0x73, 0x71, 0x56, 0x3F, 0xBF, 0x1F, 0x3C, 0x9E, 0xDF, 0x6B, 0x11,
    0x82, 0x5A, 0x5D, 0x0A,
];

#[derive(Clone)]
struct BackendState {
    inner: Arc<Mutex<Backend>>,
}

struct Backend {
    paths: Paths,
    database_path: PathBuf,
    scan_cancelled: Arc<AtomicBool>,
}

#[derive(Clone, Debug)]
struct Paths {
    game_root: PathBuf,
    mod_root: PathBuf,
    profiles_root: PathBuf,
    steam_profiles_root: Option<PathBuf>,
    cloud_profiles_root: Option<PathBuf>,
    workshop_root: Option<PathBuf>,
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
}

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

impl Paths {
    fn detect() -> Self {
        let documents = std::env::var_os("USERPROFILE")
            .map(PathBuf::from)
            .map(|p| p.join("Documents"))
            .unwrap_or_else(|| PathBuf::from("."));
        let game_root = documents.join("Euro Truck Simulator 2");
        let profiles_root = game_root.join("profiles");
        let steam_profiles = game_root.join("steam_profiles");
        let steam_profiles_root = steam_profiles.is_dir().then_some(steam_profiles);
        let steam_roots = [
            PathBuf::from(r"C:\Program Files (x86)\Steam"),
            PathBuf::from(r"C:\Program Files\Steam"),
            PathBuf::from(r"E:\SteamLibrary"),
            PathBuf::from(r"D:\SteamLibrary"),
            PathBuf::from(r"F:\SteamLibrary"),
            PathBuf::from(r"G:\SteamLibrary"),
        ];
        let workshop_root = steam_roots
            .iter()
            .map(|root| root.join("steamapps/workshop/content/227300"))
            .find(|candidate| candidate.is_dir());
        let cloud_profiles_root = find_cloud_profiles(&steam_roots);
        let game_executable = find_game_executable(&steam_roots);
        Self {
            mod_root: game_root.join("mod"),
            game_root,
            profiles_root,
            steam_profiles_root,
            cloud_profiles_root,
            workshop_root,
            game_executable,
        }
    }
}

fn find_game_executable(steam_roots: &[PathBuf]) -> Option<PathBuf> {
    for root in steam_roots {
        let common = root.join("steamapps/common");
        for (directory, executable) in [
            ("Euro Truck Simulator 2", "eurotrucks2.exe"),
            ("American Truck Simulator", "amtrucks.exe"),
        ] {
            for architecture in ["win_x64", "win_x86"] {
                let candidate = common.join(directory).join("bin").join(architecture).join(executable);
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
    let decrypted = cbc_decrypt(&bytes[56..], &iv)?;
    let pad = *decrypted.last().ok_or("ScsC payload is empty")? as usize;
    if pad == 0 || pad > 16 || pad > decrypted.len() || decrypted[decrypted.len() - pad..].iter().any(|x| *x as usize != pad) {
        return Err("ScsC padding is invalid".into());
    }
    let decrypted = &decrypted[..decrypted.len() - pad];
    let mut decoder = ZlibDecoder::new(decrypted);
    let mut output = Vec::new();
    decoder
        .read_to_end(&mut output)
        .map_err(|e| format!("ScsC decompress failed: {e}"))?;
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
    output.extend_from_slice(&(plain.len() as i32).to_le_bytes());
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
        let prefix = format!("{field}:");
        let value = trimmed.strip_prefix(&prefix)?.trim();
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
    result.extend(list_profiles_from(paths.steam_profiles_root.as_deref(), "steam"));
    result.extend(list_profiles_from(paths.cloud_profiles_root.as_deref(), "cloud"));
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
               scanned_at_ms INTEGER NOT NULL
             );
             CREATE INDEX IF NOT EXISTS ix_mod_display_v2 ON mod_package_v2(display_name COLLATE NOCASE);
             CREATE TABLE IF NOT EXISTS preset (
               profile_id TEXT NOT NULL,
               name TEXT NOT NULL,
               active_mods_json TEXT NOT NULL,
               PRIMARY KEY(profile_id, name)
             );",
        )
        .map_err(|e| format!("initialize database failed: {e}"))?;
    Ok(connection)
}

fn manifest_for(path: &Path) -> (String, String, String, String) {
    let manifest = archive_core::read_manifest(path).unwrap_or_default();
    (
        manifest.package_name,
        manifest.display_name,
        manifest.author,
        manifest.version,
    )
}

fn directory_signature(path: &Path, cancelled: &AtomicBool) -> (u64, i64) {
    let mut stack = vec![path.to_path_buf()];
    let mut total_size = 0u64;
    let mut latest_modified = 0i64;
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
            continue;
        }
        let Ok(entries) = fs::read_dir(&current) else {
            continue;
        };
        for entry in entries.flatten() {
            stack.push(entry.path());
        }
    }
    (total_size, latest_modified)
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
        let (size, modified_ms) = if is_dir {
            directory_signature(&path, cancelled)
        } else {
            let size = metadata.as_ref().map(|value| value.len()).unwrap_or_default();
            let modified_ms = metadata
                .and_then(|value| value.modified().ok())
                .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
                .map(|value| value.as_millis() as i64)
                .unwrap_or_default();
            (size, modified_ms)
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
            display_name: id.replace('_', " "),
            author: String::new(),
            version: String::new(),
            size,
            modified_ms,
            enabled: false,
            category: "unknown".into(),
        });
    }
    result
}

fn load_cached(connection: &Connection) -> Result<Vec<ModDto>, String> {
    let mut statement = connection
        .prepare(
            "SELECT mod_id, package_name, path, package_type, display_name, author, version, size, modified_ms
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
            })
        })
        .map_err(|e| format!("read mod index failed: {e}"))?;
    rows.map(|row| row.map_err(|e| format!("read mod row failed: {e}")))
        .collect()
}

fn sync_index(connection: &mut Connection, incoming: &[ModDto]) -> Result<ScanSummary, String> {
    let started = std::time::Instant::now();
    let cached = load_cached(connection)?;
    let old: HashMap<String, (i64, u64)> = cached
        .iter()
        .map(|m| (m.path.clone(), (m.modified_ms, m.size)))
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
            .map(|(modified, size)| *modified != mod_row.modified_ms || *size != mod_row.size)
            .unwrap_or(true);
        if !changed {
            continue;
        }
        if old.contains_key(&mod_row.path) {
            updated += 1;
        } else {
            added += 1;
        }
        let mut enriched = mod_row.clone();
        let previous = cached.iter().find(|row| row.path == mod_row.path);
        let (package_name, display_name, author, version) = manifest_for(Path::new(&mod_row.path));
        if !package_name.is_empty() {
            enriched.package_name = package_name;
        } else if let Some(previous) = previous {
            enriched.package_name = previous.package_name.clone();
        }
        if !display_name.is_empty() {
            enriched.display_name = display_name;
        } else if let Some(previous) = previous {
            enriched.display_name = previous.display_name.clone();
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
            "INSERT INTO mod_package_v2(path, mod_id, package_name, package_type, display_name, author, version, size, modified_ms, scanned_at_ms)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)
             ON CONFLICT(path) DO UPDATE SET mod_id=excluded.mod_id,
               package_name=excluded.package_name, package_type=excluded.package_type,
               display_name=excluded.display_name, author=excluded.author, version=excluded.version,
               size=excluded.size, modified_ms=excluded.modified_ms, scanned_at_ms=excluded.scanned_at_ms",
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
        row.enabled = active.iter().any(|entry| rows_match(&row.package_name, entry));
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
        .map(|line| line.chars().take_while(|c| c.is_whitespace()).collect::<String>())
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
        let at = lines.iter().position(|x| x.trim() == "}").unwrap_or(lines.len());
        lines.insert(at, format!("{indent}active_mods: {}", active_mods.len()));
    }
    let insert_at = lines
        .iter()
        .position(|line| line.trim_start().starts_with("active_mods:"))
        .map(|index| index + 1)
        .unwrap_or(lines.len());
    let new_lines = active_mods.iter().enumerate().map(|(index, value)| {
        format!("{indent}active_mods[{index}]: \"{}\"", escape_sii(value))
    });
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
            Some(error) => format!("active_mods write verification failed; restoring backup failed: {error}"),
            None => "active_mods write verification failed; original profile restored from backup.".into(),
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

#[tauri::command(rename_all = "camelCase")]
fn profile_list(state: State<'_, BackendState>) -> Result<Vec<ProfileDto>, String> {
    let backend = state.inner.lock().map_err(|_| "backend lock poisoned".to_string())?;
    Ok(all_profiles(&backend.paths))
}

#[tauri::command(rename_all = "camelCase")]
fn profile_read_active(
    profile_id: String,
    state: State<'_, BackendState>,
) -> Result<Vec<String>, String> {
    let backend = state.inner.lock().map_err(|_| "backend lock poisoned".to_string())?;
    let profile = find_profile(&backend.paths, &profile_id).ok_or("Profile not found.")?;
    active_for_profile(&profile)
}

#[tauri::command(rename_all = "camelCase")]
fn profile_write_active(
    request: WriteActiveRequest,
    state: State<'_, BackendState>,
) -> Result<SaveResult, String> {
    let backend = state.inner.lock().map_err(|_| "backend lock poisoned".to_string())?;
    let profile = find_profile(&backend.paths, &request.profile_id).ok_or("Profile not found.")?;
    replace_active(&profile, &request.active_mods)
}

#[tauri::command(rename_all = "camelCase")]
fn mod_list(profile_id: String, state: State<'_, BackendState>) -> Result<Vec<ModDto>, String> {
    let backend = state.inner.lock().map_err(|_| "backend lock poisoned".to_string())?;
    let db = open_db(&backend.database_path)?;
    let mut mods = dedupe_mods(load_cached(&db)?);
    if let Some(profile) = find_profile(&backend.paths, &profile_id) {
        let active = active_for_profile(&profile)?;
        apply_enabled(&mut mods, &active);
        let mut by_key: HashMap<String, ModDto> = mods.into_iter().fold(HashMap::new(), |mut rows, row| {
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

#[tauri::command(rename_all = "camelCase")]
fn mod_scan(state: State<'_, BackendState>) -> Result<ScanSummary, String> {
    let (paths, database_path, cancelled) = {
        let backend = state.inner.lock().map_err(|_| "backend lock poisoned".to_string())?;
        (
            backend.paths.clone(),
            backend.database_path.clone(),
            Arc::clone(&backend.scan_cancelled),
        )
    };
    cancelled.store(false, Ordering::Relaxed);
    let mut discovered = discover_packages(Some(&paths.mod_root), false, &cancelled);
    discovered.extend(discover_packages(paths.workshop_root.as_deref(), true, &cancelled));
    if cancelled.load(Ordering::Relaxed) {
        return Err("Scan cancelled.".into());
    }
    let mut db = open_db(&database_path)?;
    sync_index(&mut db, &discovered)
}

#[tauri::command(rename_all = "camelCase")]
fn mod_cancel(state: State<'_, BackendState>) -> Result<(), String> {
    let backend = state.inner.lock().map_err(|_| "backend lock poisoned".to_string())?;
    backend.scan_cancelled.store(true, Ordering::Relaxed);
    Ok(())
}

#[tauri::command(rename_all = "camelCase")]
fn mod_set_enabled(
    profile_id: String,
    package_name: String,
    enabled: bool,
    state: State<'_, BackendState>,
) -> Result<Vec<String>, String> {
    let backend = state.inner.lock().map_err(|_| "backend lock poisoned".to_string())?;
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

#[tauri::command(rename_all = "camelCase")]
fn mod_move(request: MoveRequest, state: State<'_, BackendState>) -> Result<SaveResult, String> {
    let backend = state.inner.lock().map_err(|_| "backend lock poisoned".to_string())?;
    let profile = find_profile(&backend.paths, &request.profile_id).ok_or("Profile not found.")?;
    replace_active(&profile, &request.active_mods)
}

#[tauri::command(rename_all = "camelCase")]
fn preset_list(
    profile_id: String,
    state: State<'_, BackendState>,
) -> Result<Vec<PresetDto>, String> {
    let backend = state.inner.lock().map_err(|_| "backend lock poisoned".to_string())?;
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

#[tauri::command(rename_all = "camelCase")]
fn preset_save(request: PresetRequest, state: State<'_, BackendState>) -> Result<(), String> {
    let active_mods = request.active_mods.ok_or("active_mods is required.")?;
    if request.name.trim().is_empty() {
        return Err("Preset name is required.".into());
    }
    let backend = state.inner.lock().map_err(|_| "backend lock poisoned".to_string())?;
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

#[tauri::command(rename_all = "camelCase")]
fn preset_load(
    request: PresetRequest,
    state: State<'_, BackendState>,
) -> Result<Vec<String>, String> {
    let backend = state.inner.lock().map_err(|_| "backend lock poisoned".to_string())?;
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

#[tauri::command(rename_all = "camelCase")]
fn preset_delete(
    request: PresetRequest,
    state: State<'_, BackendState>,
) -> Result<bool, String> {
    let backend = state.inner.lock().map_err(|_| "backend lock poisoned".to_string())?;
    let db = open_db(&backend.database_path)?;
    let changed = db
        .execute(
            "DELETE FROM preset WHERE profile_id = ?1 AND name = ?2",
            params![request.profile_id, request.name.trim()],
        )
        .map_err(|e| format!("delete preset failed: {e}"))?;
    Ok(changed > 0)
}

#[tauri::command(rename_all = "camelCase")]
fn save_list_local(
    profile_id: String,
    state: State<'_, BackendState>,
) -> Result<Vec<SaveSlotDto>, String> {
    let backend = state.inner.lock().map_err(|_| "backend lock poisoned".to_string())?;
    let profile = find_profile(&backend.paths, &profile_id).ok_or("Profile not found.")?;
    if !profile.writable {
        return Ok(Vec::new());
    }
    let root = Path::new(&profile.folder).join("save");
    let mut rows: Vec<(u8, String, SaveSlotDto)> = Vec::new();
    if let Ok(entries) = fs::read_dir(root) {
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
    }
    rows.sort_by(|a, b| a.0.cmp(&b.0).then_with(|| a.1.cmp(&b.1)));
    Ok(rows.into_iter().map(|(_, _, row)| row).collect())
}

#[tauri::command(rename_all = "camelCase")]
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
        return Err(format!("Game executable does not exist: {}", executable.display()));
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
    fn active_mod_parser_keeps_game_order() {
        let text = "profile : .profile {\n active_mods[0]: \"high\"\n active_mods[1]: \"low\"\n}";
        assert_eq!(parse_active_mods(text), ["high", "low"]);
        assert_eq!(profile_order_to_ui(&parse_active_mods(text)), ["low", "high"]);
    }

    #[test]
    fn canonical_package_handles_storage_aliases() {
        assert_eq!(canonical_package("mod_workshop_package.0000002A|workshop"), "mod_workshop_package.0000002a");
        assert_eq!(canonical_package("demo_local"), "demo");
        assert_eq!(canonical_package("demo_copy12"), "demo");
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
        };
        let first_summary = sync_index(&mut connection, &[first.clone(), second.clone()]).expect("first sync");
        assert_eq!((first_summary.added, first_summary.updated, first_summary.removed), (2, 0, 0));
        let second_summary = sync_index(&mut connection, &[first.clone()]).expect("second sync");
        assert_eq!((second_summary.added, second_summary.updated, second_summary.removed), (0, 0, 1));
        let cached = load_cached(&connection).expect("cached");
        assert_eq!(cached.len(), 1);
        assert_eq!(cached[0].path, first.path);
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
               scanned_at_ms INTEGER NOT NULL
             );
             CREATE TABLE IF NOT EXISTS preset (
               profile_id TEXT NOT NULL,
               name TEXT NOT NULL,
               active_mods_json TEXT NOT NULL,
               PRIMARY KEY(profile_id, name)
             );",
        )
        .map_err(|e| format!("initialize database failed: {e}"))
}

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
            })),
        })
        .invoke_handler(tauri::generate_handler![
            profile_list,
            profile_read_active,
            profile_write_active,
            mod_list,
            mod_scan,
            mod_cancel,
            mod_set_enabled,
            mod_move,
            preset_list,
            preset_save,
            preset_load,
            preset_delete,
            save_list_local,
            game_launch
        ])
        .run(tauri::generate_context!())
        .expect("error while running ETS2 Mod Manager");
}
