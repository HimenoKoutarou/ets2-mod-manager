//! Filesystem scanner used by the worker process.

use archive_core::{read_manifest, Manifest};
use std::path::{Path, PathBuf};

pub fn supported_package(path: &str) -> bool {
    let lower = path.to_ascii_lowercase();
    lower.ends_with(".scs") || lower.ends_with(".zip")
}

pub fn count_supported<I>(paths: I) -> usize
where
    I: IntoIterator,
    I::Item: AsRef<str>,
{
    paths
        .into_iter()
        .filter(|path| supported_package(path.as_ref()))
        .count()
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScannedPackage {
    pub mod_id: String,
    pub path: PathBuf,
    pub package_type: String,
    pub size: u64,
    pub modified_unix_ms: i128,
    pub manifest: Manifest,
}

pub fn scan_roots(local: Option<&Path>, workshop: Option<&Path>) -> Vec<ScannedPackage> {
    let mut output = Vec::new();
    scan_root(&mut output, local, false);
    scan_root(&mut output, workshop, true);
    output.sort_by(|a, b| a.path.cmp(&b.path));
    output
}

fn scan_root(output: &mut Vec<ScannedPackage>, root: Option<&Path>, workshop: bool) {
    let Some(root) = root.filter(|p| p.is_dir()) else {
        return;
    };
    let Ok(entries) = std::fs::read_dir(root) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let is_dir = path.is_dir();
        let ext = path
            .extension()
            .and_then(|v| v.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if !is_dir && ext != "scs" && ext != "zip" {
            continue;
        }
        if is_dir && workshop {}
        let metadata = std::fs::metadata(&path).ok();
        let size = metadata
            .as_ref()
            .map(|m| {
                if is_dir {
                    directory_size(&path)
                } else {
                    m.len()
                }
            })
            .unwrap_or(0);
        let modified = metadata
            .and_then(|m| m.modified().ok())
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_millis() as i128)
            .unwrap_or(0);
        let package_type = if workshop {
            "workshop"
        } else if is_dir {
            "directory"
        } else {
            ext.as_str()
        };
        let mod_id = if workshop {
            path.file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("")
                .to_string()
        } else {
            path.file_stem()
                .and_then(|n| n.to_str())
                .unwrap_or("")
                .to_string()
        };
        let manifest = read_manifest(&path).unwrap_or_default();
        output.push(ScannedPackage {
            mod_id,
            path,
            package_type: package_type.to_string(),
            size,
            modified_unix_ms: modified,
            manifest,
        });
    }
}

fn directory_size(path: &Path) -> u64 {
    std::fs::read_dir(path)
        .ok()
        .into_iter()
        .flatten()
        .map(|entry| {
            let p = entry.ok().map(|e| e.path());
            p.map(|p| {
                if p.is_dir() {
                    directory_size(&p)
                } else {
                    std::fs::metadata(p).map(|m| m.len()).unwrap_or(0)
                }
            })
            .unwrap_or(0)
        })
        .sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counts_scs_and_zip_only() {
        assert_eq!(count_supported(["a.scs", "b.zip", "folder", "c.SCS"]), 3);
    }
}
