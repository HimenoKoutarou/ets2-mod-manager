//! Archive detection and lightweight manifest extraction.

use std::fs::File;
use std::io::Read;
use std::path::Path;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArchiveKind {
    Zip,
    HashFs,
    Aem,
    Unknown,
}

impl ArchiveKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Zip => "zip",
            Self::HashFs => "scs_hashfs",
            Self::Aem => "aem",
            Self::Unknown => "unknown",
        }
    }
}

pub fn detect_kind(bytes: &[u8]) -> ArchiveKind {
    if bytes.starts_with(b"SCS#") {
        ArchiveKind::HashFs
    } else if bytes.starts_with(b"AEM!") {
        ArchiveKind::Aem
    } else if bytes.starts_with(b"PK") {
        ArchiveKind::Zip
    } else {
        ArchiveKind::Unknown
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Manifest {
    pub package_name: String,
    pub display_name: String,
    pub author: String,
    pub version: String,
    pub icon_filename: String,
}

impl Default for Manifest {
    fn default() -> Self {
        Self {
            package_name: String::new(),
            display_name: String::new(),
            author: String::new(),
            version: String::new(),
            icon_filename: String::new(),
        }
    }
}

pub fn parse_manifest(text: &str) -> Manifest {
    let mut manifest = Manifest::default();
    for line in text.lines() {
        let Some((key, raw)) = line.split_once(':') else {
            continue;
        };
        let key = key.trim();
        let value = strip_inline_comment(raw).trim().trim_end_matches(',').trim();
        if key == "mod_package" {
            if manifest.package_name.is_empty() {
                if let Some(name) = value.split_whitespace().next() {
                    let candidate = name.trim_end_matches('{').trim();
                    if !is_placeholder_package_name(candidate) {
                        manifest.package_name = candidate.to_string();
                    }
                }
            }
            continue;
        }
        let value = value
            .strip_prefix('"')
            .and_then(|v| v.strip_suffix('"'))
            .unwrap_or(value);
        let target = match key {
            "package_name" => &mut manifest.package_name,
            "display_name" | "name" => &mut manifest.display_name,
            "author" => &mut manifest.author,
            "version" | "package_version" => &mut manifest.version,
            "icon" | "icon_filename" => &mut manifest.icon_filename,
            _ => continue,
        };
        if target.is_empty() {
            *target = value.replace("\\\"", "\"");
        }
    }
    manifest
}

fn strip_inline_comment(value: &str) -> &str {
    let mut quoted = false;
    let mut escaped = false;
    for (index, character) in value.char_indices() {
        if escaped {
            escaped = false;
            continue;
        }
        if character == '\\' && quoted {
            escaped = true;
            continue;
        }
        if character == '"' {
            quoted = !quoted;
        } else if character == '#' && !quoted {
            return &value[..index];
        }
    }
    value
}

fn is_placeholder_package_name(value: &str) -> bool {
    let trimmed = value.trim();
    let normalized = trimmed.trim_start_matches('.').to_ascii_lowercase();
    trimmed.starts_with('.')
        || normalized.is_empty()
        || matches!(
            normalized.as_str(),
            "manifest" | "package_name" | "mods_info" | "nameless" | "mod_package"
        )
}

pub fn read_manifest(path: impl AsRef<Path>) -> Result<Manifest, String> {
    let path = path.as_ref();
    if path.is_dir() {
        let candidate = path.join("manifest.sii");
        if candidate.exists() {
            return read_manifest(&candidate);
        }
        let mut stack = vec![path.to_path_buf()];
        while let Some(dir) = stack.pop() {
            for entry in std::fs::read_dir(&dir).map_err(|e| e.to_string())? {
                let entry = entry.map_err(|e| e.to_string())?;
                let p = entry.path();
                if p.is_dir() {
                    stack.push(p);
                } else if p
                    .file_name()
                    .is_some_and(|n| n.eq_ignore_ascii_case("manifest.sii"))
                {
                    return read_manifest(p);
                }
            }
        }
        return Ok(Manifest::default());
    }
    let mut file = File::open(path).map_err(|e| e.to_string())?;
    let mut header = [0u8; 4];
    if file.read_exact(&mut header).is_err() {
        return Ok(Manifest::default());
    }

    // HashFS/AEM SCS packages can be several gigabytes. They are not ZIP
    // containers, so reading the entire file just to look for manifest.sii
    // is both wasteful and a major source of scan latency.
    if &header == b"SCS#" || &header == b"AEM!" {
        return Ok(Manifest::default());
    }
    if &header != b"PK\x03\x04" {
        return Ok(Manifest::default());
    }

    // Stored ZIP manifests are parsed without an archive dependency. Bound
    // the read so a malformed or enormous ZIP cannot turn metadata scanning
    // into a multi-gigabyte allocation; managed extraction remains available
    // for the rare package that needs deeper inspection.
    const MAX_INLINE_ZIP_BYTES: u64 = 128 * 1024 * 1024;
    if file
        .metadata()
        .map(|m| m.len())
        .unwrap_or(MAX_INLINE_ZIP_BYTES + 1)
        > MAX_INLINE_ZIP_BYTES
    {
        return Ok(Manifest::default());
    }
    let mut bytes = header.to_vec();
    file.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
    if let Some(text) = read_stored_zip_manifest(&bytes) {
        return Ok(parse_manifest(&text));
    }
    Ok(Manifest::default())
}

// SCS files that are ZIP containers commonly store manifest.sii uncompressed.
// Reading local headers directly keeps this core dependency-free and portable;
// deflated entries remain delegated to the full archive backend.
fn read_stored_zip_manifest(bytes: &[u8]) -> Option<String> {
    let mut pos = 0usize;
    while pos.checked_add(30)? <= bytes.len() {
        if &bytes[pos..pos + 4] != b"PK\x03\x04" {
            break;
        }
        let method = u16::from_le_bytes(bytes[pos + 8..pos + 10].try_into().ok()?);
        let compressed = u32::from_le_bytes(bytes[pos + 18..pos + 22].try_into().ok()?) as usize;
        let name_len = u16::from_le_bytes(bytes[pos + 26..pos + 28].try_into().ok()?) as usize;
        let extra_len = u16::from_le_bytes(bytes[pos + 28..pos + 30].try_into().ok()?) as usize;
        let header_end = pos
            .checked_add(30)?
            .checked_add(name_len)?
            .checked_add(extra_len)?;
        let data_end = header_end.checked_add(compressed)?;
        if data_end > bytes.len() {
            return None;
        }
        let name = std::str::from_utf8(&bytes[pos + 30..pos + 30 + name_len]).ok()?;
        if method == 0
            && name
                .rsplit('/')
                .next()
                .is_some_and(|n| n.eq_ignore_ascii_case("manifest.sii"))
        {
            return String::from_utf8(bytes[header_end..data_end].to_vec()).ok();
        }
        pos = data_end;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classifies_known_headers() {
        assert_eq!(detect_kind(b"PK\x03\x04"), ArchiveKind::Zip);
        assert_eq!(detect_kind(b"SCS#\0\0"), ArchiveKind::HashFs);
        assert_eq!(detect_kind(b"AEM!\0\0"), ArchiveKind::Aem);
        assert_eq!(detect_kind(b"nope"), ArchiveKind::Unknown);
    }

    #[test]
    fn parses_manifest_fields_without_a_full_sii_parser() {
        let manifest = parse_manifest("mod_package : demo {\ndisplay_name: \"Demo Map\"\nauthor: \"Team\"\npackage_version: \"1.2\"");
        assert_eq!(manifest.package_name, "demo");
        assert_eq!(manifest.display_name, "Demo Map");
        assert_eq!(manifest.author, "Team");
        assert_eq!(manifest.version, "1.2");
    }

    #[test]
    fn ignores_template_package_names_so_scanner_can_use_mod_id() {
        for placeholder in [".package_name", ".manifest", ".mods_info"] {
            let manifest = parse_manifest(&format!(
                "mod_package : {placeholder} {{\ndisplay_name: \"Demo\""
            ));
            assert!(
                manifest.package_name.is_empty(),
                "placeholder leaked: {placeholder}"
            );
            assert_eq!(manifest.display_name, "Demo");
        }
    }

    #[test]
    fn strips_manifest_comments_from_quoted_values() {
        let manifest = parse_manifest(
            "mod_package : .package_name {\n\
             display_name: \"Project Russia\" # comment\n\
             icon: \"project_russia.jpg\" # icon comment\n\
             package_version: \"5.6.3a\"",
        );
        assert_eq!(manifest.display_name, "Project Russia");
        assert_eq!(manifest.icon_filename, "project_russia.jpg");
        assert_eq!(manifest.version, "5.6.3a");
    }
}
