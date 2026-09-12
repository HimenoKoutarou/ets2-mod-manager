use serde::{Deserialize, Serialize};
use sha1::{Digest, Sha1};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};

type Result<T> = std::result::Result<T, String>;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Status {
    pub game_path: String,
    pub actual_path: String,
    pub kind: String,
    pub recovery_pending: bool,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Progress {
    pub phase: String,
    pub path: String,
    pub completed: u64,
    pub total: u64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Outcome {
    pub status: Status,
    pub retained_path: Option<String>,
}

#[derive(Serialize, Deserialize)]
struct Journal {
    root: PathBuf,
    source: PathBuf,
    destination: PathBuf,
    staging: PathBuf,
    backup: PathBuf,
    operation: String,
}

fn message(e: impl std::fmt::Display) -> String {
    e.to_string()
}

fn journal_path(root: &Path) -> PathBuf {
    root.with_file_name("ets2modmanager-directory-operation.json")
}

// OS file locks also serialize multiple running copies of this application.
pub fn lock(root: &Path, exclusive: bool) -> Result<File> {
    let file = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(root.with_file_name("ets2modmanager-directory.lock"))
        .map_err(message)?;
    if exclusive {
        file.try_lock().map_err(message)?;
    } else {
        file.try_lock_shared().map_err(message)?;
    }
    Ok(file)
}

pub fn read_lock(root: &Path) -> Result<File> {
    let file = lock(root, false).map_err(|e| format!("Mod directory is busy: {e}"))?;
    if journal_path(root).exists() {
        return Err("Mod directory recovery is required. Open Mod directory settings.".into());
    }
    Ok(file)
}

fn is_link(path: &Path) -> bool {
    fs::symlink_metadata(path)
        .map(|m| {
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                m.file_attributes() & 0x400 != 0
            }
            #[cfg(not(windows))]
            m.file_type().is_symlink()
        })
        .unwrap_or(false)
}

fn link_target(path: &Path) -> Result<PathBuf> {
    fs::read_link(path).map_err(message).map(|target| {
        if target.is_absolute() {
            target
        } else {
            path.parent().unwrap().join(target)
        }
    })
}

pub fn status(root: &Path) -> Result<Status> {
    let linked = is_link(root);
    let actual = if linked {
        link_target(root)?
    } else {
        root.to_path_buf()
    };
    Ok(Status {
        game_path: display(root),
        actual_path: display(&actual),
        kind: if linked && !actual.is_dir() {
            "broken"
        } else if linked {
            "linked"
        } else if root.is_dir() {
            "local"
        } else {
            "missing"
        }
        .into(),
        recovery_pending: journal_path(root).exists(),
    })
}

fn display(path: &Path) -> String {
    path.to_string_lossy()
        .trim_start_matches(r"\\?\")
        .to_string()
}

#[cfg(windows)]
fn create_link(target: &Path, link: &Path) -> Result<()> {
    junction::create(target, link).map_err(message)
}

#[cfg(not(windows))]
fn create_link(target: &Path, link: &Path) -> Result<()> {
    std::os::unix::fs::symlink(target, link).map_err(message)
}

fn remove_link(path: &Path) -> Result<()> {
    if !is_link(path) {
        return Err(format!("Not a directory link: {}", path.display()));
    }
    #[cfg(windows)]
    return fs::remove_dir(path).map_err(message);
    #[cfg(not(windows))]
    fs::remove_file(path).map_err(message)
}

fn resolved(path: &Path) -> Result<PathBuf> {
    if !path.is_absolute()
        || path
            .components()
            .any(|c| c == std::path::Component::ParentDir)
    {
        return Err("An absolute directory path without '..' is required.".into());
    }
    if path.exists() {
        fs::canonicalize(path).map_err(message)
    } else {
        let parent = path.parent().ok_or("Directory parent is missing.")?;
        Ok(fs::canonicalize(parent).map_err(message)?.join(
            path.file_name()
                .ok_or("Choose a directory, not a drive root.")?,
        ))
    }
}

fn overlaps(a: &Path, b: &Path) -> bool {
    let a = display(a)
        .replace('/', "\\")
        .trim_end_matches('\\')
        .to_lowercase();
    let b = display(b)
        .replace('/', "\\")
        .trim_end_matches('\\')
        .to_lowercase();
    a == b || a.starts_with(&(b.clone() + "\\")) || b.starts_with(&(a + "\\"))
}

fn inventory(
    root: &Path,
    files: &mut Vec<PathBuf>,
    progress: &mut impl FnMut(Progress),
) -> Result<()> {
    progress(Progress {
        phase: "preflight".into(),
        path: display(root),
        completed: 0,
        total: 0,
    });
    for entry in fs::read_dir(root).map_err(message)? {
        let path = entry.map_err(message)?.path();
        if is_link(&path) {
            return Err(format!(
                "Nested directory links are not supported: {}",
                display(&path)
            ));
        }
        if path.is_dir() {
            inventory(&path, files, progress)?;
        } else if path.is_file() {
            files.push(path);
        } else {
            return Err(format!("Unsupported file: {}", display(&path)));
        }
    }
    Ok(())
}

fn copy_tree(
    source: &Path,
    target: &Path,
    completed: &mut u64,
    total: u64,
    progress: &mut impl FnMut(Progress),
) -> Result<()> {
    fs::create_dir(target).map_err(message)?;
    for entry in fs::read_dir(source).map_err(message)? {
        let path = entry.map_err(message)?.path();
        let destination = target.join(path.file_name().unwrap());
        if is_link(&path) {
            return Err(format!(
                "Directory changed during migration: {}",
                display(&path)
            ));
        }
        if path.is_dir() {
            copy_tree(&path, &destination, completed, total, progress)?;
            continue;
        }
        let mut input = File::open(&path).map_err(message)?;
        let before = input.metadata().map_err(message)?;
        let mut output = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&destination)
            .map_err(message)?;
        let mut hash = Sha1::new();
        let mut buffer = vec![0; 1024 * 1024];
        loop {
            let count = input.read(&mut buffer).map_err(message)?;
            if count == 0 {
                break;
            }
            output.write_all(&buffer[..count]).map_err(message)?;
            hash.update(&buffer[..count]);
            *completed += count as u64;
            progress(Progress {
                phase: "copy".into(),
                path: display(&path),
                completed: *completed,
                total,
            });
        }
        output.sync_all().map_err(message)?;
        output
            .set_modified(before.modified().map_err(message)?)
            .map_err(message)?;
        drop(output);
        let mut verify = File::open(&destination).map_err(message)?;
        let mut copied_hash = Sha1::new();
        loop {
            let count = verify.read(&mut buffer).map_err(message)?;
            if count == 0 {
                break;
            }
            copied_hash.update(&buffer[..count]);
            *completed += count as u64;
            progress(Progress {
                phase: "verify".into(),
                path: display(&path),
                completed: *completed,
                total,
            });
        }
        let after = input.metadata().map_err(message)?;
        if hash.finalize() != copied_hash.finalize()
            || before.len() != after.len()
            || before.modified().ok() != after.modified().ok()
        {
            return Err(format!(
                "File changed or verification failed: {}",
                display(&path)
            ));
        }
    }
    // Directory timestamps are part of the incremental scanner's signature.
    let modified = fs::metadata(source)
        .map_err(message)?
        .modified()
        .map_err(message)?;
    let mut options = OpenOptions::new();
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.access_mode(0x100).custom_flags(0x0200_0000);
    }
    #[cfg(not(windows))]
    options.read(true);
    options
        .open(target)
        .map_err(message)?
        .set_modified(modified)
        .map_err(message)?;
    Ok(())
}

fn write_journal(root: &Path, journal: &Journal) -> Result<()> {
    let temporary = journal.backup.with_extension("journal");
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(message)?;
    file.write_all(&serde_json::to_vec_pretty(journal).map_err(message)?)
        .map_err(message)?;
    file.sync_all().map_err(message)?;
    drop(file);
    fs::rename(temporary, journal_path(root)).map_err(message)
}

fn rollback(journal: &Journal) -> Result<()> {
    if fs::symlink_metadata(&journal.backup).is_ok() {
        if is_link(&journal.root) {
            remove_link(&journal.root)?;
        } else if journal.root.exists() {
            // Preserve a completed restore copy; never recursively delete it.
            fs::rename(&journal.root, &journal.staging).map_err(message)?;
        }
        fs::rename(&journal.backup, &journal.root).map_err(message)?;
    }
    Ok(())
}

pub fn recover(root: &Path, database: &Path) -> Result<Outcome> {
    let _lock = lock(root, true)?;
    let journal: Journal =
        serde_json::from_slice(&fs::read(journal_path(root)).map_err(message)?).map_err(message)?;
    if journal.root != root
        || journal.backup.parent() != root.parent()
        || journal.staging.parent() != journal.destination.parent()
        || !journal
            .backup
            .file_name()
            .is_some_and(|n| n.to_string_lossy().starts_with(".ets2mm-original-"))
        || !journal
            .staging
            .file_name()
            .is_some_and(|n| n.to_string_lossy().starts_with(".ets2mm-copy-"))
    {
        return Err("Invalid directory recovery journal.".into());
    }
    rollback(&journal)?;
    remap_cache(database, &journal.destination, &journal.source, true)?;
    fs::remove_file(journal_path(root)).map_err(message)?;
    Ok(Outcome {
        status: status(root)?,
        retained_path: Some(format!(
            "{}; {}",
            display(&journal.staging),
            display(&journal.destination)
        )),
    })
}

pub fn change(
    root: &Path,
    target: &Path,
    operation: &str,
    database: &Path,
    mut guard: impl FnMut() -> Result<()>,
    mut progress: impl FnMut(Progress),
) -> Result<Outcome> {
    change_with_link(
        root,
        target,
        operation,
        database,
        &mut guard,
        &mut progress,
        create_link,
    )
}

fn change_with_link(
    root: &Path,
    target: &Path,
    operation: &str,
    database: &Path,
    guard: &mut impl FnMut() -> Result<()>,
    progress: &mut impl FnMut(Progress),
    make_link: impl FnOnce(&Path, &Path) -> Result<()>,
) -> Result<Outcome> {
    let _lock = lock(root, true).map_err(|e| format!("Mod directory is busy: {e}"))?;
    if journal_path(root).exists() {
        return Err("Recover the previous directory operation first.".into());
    }
    guard()?;
    let current = status(root)?;
    if !matches!(operation, "relocate" | "restore" | "repair") {
        return Err("Unknown directory operation.".into());
    }
    if operation == "repair" && current.kind != "broken" {
        return Err("Repair requires a broken directory link.".into());
    }
    if operation == "restore" && current.kind != "linked" {
        return Err("Restore requires a working directory link.".into());
    }
    if operation == "relocate" && !matches!(current.kind.as_str(), "local" | "linked") {
        return Err("The current Mod directory is not available.".into());
    }
    let source = if operation == "repair" {
        PathBuf::from(&current.actual_path)
    } else {
        fs::canonicalize(root).map_err(message)?
    };
    let destination = if operation == "restore" {
        fs::canonicalize(root.parent().unwrap())
            .map_err(message)?
            .join(root.file_name().unwrap())
    } else {
        if is_link(target) {
            return Err("The target must be a real directory, not another link.".into());
        }
        resolved(target)?
    };
    let logical_root = fs::canonicalize(root.parent().unwrap())
        .map_err(message)?
        .join(root.file_name().unwrap());
    if overlaps(&source, &destination)
        || (operation != "restore" && overlaps(&logical_root, &destination))
    {
        return Err("Source and target cannot be the same or contain one another.".into());
    }
    if operation == "repair" {
        if !destination.is_dir() {
            return Err("Repair target must be an existing Mod directory.".into());
        }
    } else if operation != "restore"
        && destination.exists()
        && fs::read_dir(&destination)
            .map_err(message)?
            .next()
            .is_some()
    {
        return Err(
            "The target directory must be empty. Existing files will not be overwritten.".into(),
        );
    }
    let mut random = [0u8; 8];
    getrandom::fill(&mut random).map_err(message)?;
    let suffix = u64::from_le_bytes(random);
    let journal = Journal {
        root: root.to_path_buf(),
        source: source.clone(),
        destination: destination.clone(),
        staging: destination.with_file_name(format!(".ets2mm-copy-{suffix:x}")),
        backup: root.with_file_name(format!(".ets2mm-original-{suffix:x}")),
        operation: operation.into(),
    };
    let mut files = Vec::new();
    let mut preflight = Vec::new();
    if operation != "repair" {
        inventory(&source, &mut files, progress)?;
        for path in &files {
            let metadata = fs::metadata(path).map_err(message)?;
            preflight.push((
                path.clone(),
                metadata.len(),
                metadata.modified().map_err(message)?,
            ));
        }
    } else {
        inventory(&destination, &mut files, progress)?;
    }
    let total = preflight
        .iter()
        .map(|(_, len, _)| *len)
        .sum::<u64>()
        .saturating_mul(2);
    write_journal(root, &journal)?;
    let result: Result<()> = (|| {
        if operation != "repair" {
            copy_tree(&source, &journal.staging, &mut 0, total, progress)?;
            let mut after = Vec::new();
            inventory(&source, &mut after, &mut |_| {})?;
            if files.len() != after.len()
                || preflight.iter().any(|(path, len, time)| {
                    fs::metadata(path)
                        .map(|m| m.len() != *len || m.modified().ok() != Some(*time))
                        .unwrap_or(true)
                })
            {
                return Err(
                    "Source directory changed during migration. No switch was made.".into(),
                );
            }
        }
        guard()?;
        progress(Progress {
            phase: "switch".into(),
            path: display(&destination),
            completed: total,
            total,
        });
        if operation == "relocate" {
            if destination.exists() {
                fs::remove_dir(&destination).map_err(message)?;
            }
            fs::rename(&journal.staging, &destination).map_err(message)?;
        }
        fs::rename(root, &journal.backup).map_err(message)?;
        if operation == "restore" {
            fs::rename(&journal.staging, root).map_err(message)?;
        } else {
            make_link(&destination, root)?;
        }
        remap_cache(database, &source, &destination, false)?;
        fs::remove_file(journal_path(root)).map_err(message)?;
        Ok(())
    })();
    if let Err(error) = result {
        let rollback_result =
            rollback(&journal).and_then(|_| remap_cache(database, &destination, &source, true));
        if rollback_result.is_ok() {
            let _ = fs::remove_file(journal_path(root));
        }
        return Err(format!(
            "{error}; rollback: {}. Retained copies: {}, {}",
            rollback_result.err().unwrap_or_else(|| "OK".into()),
            display(&journal.staging),
            display(&destination)
        ));
    }
    // Retain the original files as a recovery copy. Remove only the old link.
    let retained = if is_link(&journal.backup) {
        match remove_link(&journal.backup) {
            Ok(()) => Some(display(&source)),
            Err(_) => Some(format!(
                "{}; {}",
                display(&source),
                display(&journal.backup)
            )),
        }
    } else {
        Some(display(&journal.backup))
    };
    progress(Progress {
        phase: "complete".into(),
        path: display(&destination),
        completed: total,
        total,
    });
    Ok(Outcome {
        status: status(root)?,
        retained_path: retained,
    })
}

fn remap_cache(database: &Path, source: &Path, destination: &Path, reverse: bool) -> Result<()> {
    let mut db = super::open_db(database)?;
    let tx = db.transaction().map_err(message)?;
    tx.execute_batch("CREATE TABLE IF NOT EXISTS mod_directory_cache_move (id INTEGER PRIMARY KEY CHECK(id=1), source TEXT NOT NULL, destination TEXT NOT NULL);").map_err(message)?;
    if reverse {
        let applied: bool = tx.query_row("SELECT EXISTS(SELECT 1 FROM mod_directory_cache_move WHERE id=1 AND source=?1 COLLATE NOCASE AND destination=?2 COLLATE NOCASE)",
            rusqlite::params![display(destination), display(source)], |r| r.get(0)).map_err(message)?;
        if !applied {
            return Ok(());
        }
    }
    // Parameters plus a literal prefix avoid SQL LIKE wildcard bugs in directory names.
    for (table, column) in [
        ("mod_package_v2", "path"),
        ("mod_media_cache", "path"),
        ("mod_media_cache_meta", "path"),
        ("localization_package_v2", "package_path"),
        ("localization_entry_v2", "package_path"),
        ("localization_entry_v2", "source_path"),
    ] {
        for prefix in [
            format!("{}\\", display(source)),
            format!("\\\\?\\{}\\", display(source)),
        ] {
            let replacement = if prefix.starts_with(r"\\?\") {
                format!("\\\\?\\{}\\", display(destination))
            } else {
                format!("{}\\", display(destination))
            };
            tx.execute(&format!("UPDATE {table} SET {column}=?1 || substr({column},length(?2)+1) WHERE substr({column},1,length(?2))=?2 COLLATE NOCASE"),
                rusqlite::params![replacement, prefix]).map_err(message)?;
        }
    }
    if reverse {
        tx.execute("DELETE FROM mod_directory_cache_move", [])
            .map_err(message)?;
    } else {
        tx.execute(
            "INSERT OR REPLACE INTO mod_directory_cache_move VALUES (1,?1,?2)",
            rusqlite::params![display(source), display(destination)],
        )
        .map_err(message)?;
    }
    tx.commit().map_err(message)
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;

    struct Fixture {
        base: PathBuf,
        root: PathBuf,
        db: PathBuf,
    }
    impl Fixture {
        fn new() -> Self {
            let mut id = [0; 8];
            getrandom::fill(&mut id).unwrap();
            let base = std::env::temp_dir()
                .join(format!("ets2-directory-test-{:x}", u64::from_le_bytes(id)));
            let root = base.join("mod");
            fs::create_dir_all(root.join("nested")).unwrap();
            fs::create_dir(root.join("empty")).unwrap();
            fs::write(root.join("sample.scs"), b"sample archive").unwrap();
            fs::write(root.join("nested").join("locale.sii"), b"locale data").unwrap();
            Self {
                db: base.join("cache.db"),
                base,
                root,
            }
        }
        fn run(&self, target: &Path, operation: &str) -> Result<Outcome> {
            change(&self.root, target, operation, &self.db, || Ok(()), |_| {})
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            // Only the unique test-owned directory is removed; junctions are
            // removed as links, never traversed for cleanup.
            fn clean(path: &Path) {
                if is_link(path) {
                    let _ = remove_link(path);
                    return;
                }
                if path.is_dir() {
                    for entry in fs::read_dir(path).unwrap().flatten() {
                        clean(&entry.path());
                    }
                    let _ = fs::remove_dir(path);
                } else {
                    let _ = fs::remove_file(path);
                }
            }
            assert!(self
                .base
                .file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("ets2-directory-test-"));
            clean(&self.base);
        }
    }

    #[test]
    fn migrate_relocate_again_and_restore_preserve_bytes_timestamps_and_empty_dirs() {
        let f = Fixture::new();
        let time = fs::metadata(f.root.join("sample.scs"))
            .unwrap()
            .modified()
            .unwrap();
        let first = f.base.join("mods & 中文");
        let second = f.base.join("other");
        let outcome = f.run(&first, "relocate").unwrap();
        assert_eq!(outcome.status.kind, "linked");
        assert!(Path::new(&outcome.retained_path.unwrap())
            .join("sample.scs")
            .is_file());
        assert_eq!(
            fs::read(f.root.join("sample.scs")).unwrap(),
            b"sample archive"
        );
        assert!(first.join("empty").is_dir());
        assert_eq!(
            fs::metadata(first.join("sample.scs"))
                .unwrap()
                .modified()
                .unwrap(),
            time
        );
        // Status is filesystem-backed, so reopening the app needs no scan.
        assert_eq!(status(&f.root).unwrap().actual_path, display(&first));
        f.run(&second, "relocate").unwrap();
        assert_eq!(
            fs::read(second.join("nested/locale.sii")).unwrap(),
            b"locale data"
        );
        f.run(Path::new(""), "restore").unwrap();
        assert_eq!(status(&f.root).unwrap().kind, "local");
        assert!(!is_link(&f.root));
        assert!(second.join("sample.scs").is_file());
        assert_eq!(
            fs::metadata(f.root.join("sample.scs"))
                .unwrap()
                .modified()
                .unwrap(),
            time
        );
    }

    #[test]
    fn rejects_collisions_overlaps_and_nested_links_without_changing_source() {
        let f = Fixture::new();
        let occupied = f.base.join("occupied");
        fs::create_dir(&occupied).unwrap();
        fs::write(occupied.join("other.scs"), b"do not overwrite").unwrap();
        assert!(f.run(&occupied, "relocate").unwrap_err().contains("empty"));
        assert!(f.run(&f.root, "relocate").is_err());
        assert!(f.run(&f.root.join("child"), "relocate").is_err());
        assert!(f.run(&f.base, "relocate").is_err());
        create_link(&occupied, &f.root.join("nested-link")).unwrap();
        assert!(f
            .run(&f.base.join("new"), "relocate")
            .unwrap_err()
            .contains("Nested"));
        assert_eq!(
            fs::read(f.root.join("sample.scs")).unwrap(),
            b"sample archive"
        );
        assert_eq!(
            fs::read(occupied.join("other.scs")).unwrap(),
            b"do not overwrite"
        );
    }

    #[test]
    fn link_failure_and_game_start_during_copy_roll_back() {
        let f = Fixture::new();
        let target = f.base.join("destination");
        let error = change_with_link(
            &f.root,
            &target,
            "relocate",
            &f.db,
            &mut || Ok(()),
            &mut |_| {},
            |_, _| Err("injected link failure".into()),
        )
        .unwrap_err();
        assert!(error.contains("rollback: OK"), "{error}");
        assert!(!is_link(&f.root));
        assert!(f.root.join("sample.scs").is_file());
        assert!(!journal_path(&f.root).exists());
        let mut calls = 0;
        let error = change(
            &f.root,
            &f.base.join("second"),
            "relocate",
            &f.db,
            || {
                calls += 1;
                if calls == 2 {
                    Err("game started".into())
                } else {
                    Ok(())
                }
            },
            |_| {},
        )
        .unwrap_err();
        assert!(error.contains("game started"));
        assert!(f.root.join("sample.scs").is_file());
    }

    #[test]
    fn repairs_broken_junction_without_moving_target_files() {
        let f = Fixture::new();
        let old = f.base.join("old");
        let new = f.base.join("recovered");
        f.run(&old, "relocate").unwrap();
        fs::rename(&old, &new).unwrap();
        assert_eq!(status(&f.root).unwrap().kind, "broken");
        f.run(&new, "repair").unwrap();
        assert_eq!(status(&f.root).unwrap().kind, "linked");
        assert_eq!(
            fs::read(f.root.join("sample.scs")).unwrap(),
            b"sample archive"
        );
    }

    #[test]
    fn exclusive_lock_rejects_simultaneous_scan_or_migration() {
        let f = Fixture::new();
        let shared = read_lock(&f.root).unwrap();
        assert!(lock(&f.root, true).is_err());
        drop(shared);
        let exclusive = lock(&f.root, true).unwrap();
        assert!(read_lock(&f.root).is_err());
        assert!(f.run(&f.base.join("new"), "relocate").is_err());
        drop(exclusive);
    }

    #[test]
    fn cache_prefix_remap_preserves_artwork_and_localization_without_rescan() {
        let f = Fixture::new();
        let target = f.base.join("target_%");
        let old = super::super::normalize_path(&f.root.join("sample.scs"));
        let db = super::super::open_db(&f.db).unwrap();
        db.execute("INSERT INTO mod_media_cache VALUES (?1,14,123,456,'data:image/png;base64,AA',NULL,123)", [&old]).unwrap();
        db.execute(
            "INSERT INTO localization_package_v2 VALUES (?1,'zh_cn','scs',14,123,123)",
            [&old],
        )
        .unwrap();
        db.execute("INSERT INTO localization_entry_v2 VALUES (?1,'zh_cn',0,'city.foo','foo',?1,'sample','city','native',1,1,'foo','foo')", [&old]).unwrap();
        drop(db);
        f.run(&target, "relocate").unwrap();
        let next = super::super::normalize_path(&target.join("sample.scs"));
        let db = super::super::open_db(&f.db).unwrap();
        let icon: String = db
            .query_row(
                "SELECT icon_url FROM mod_media_cache WHERE path=?1",
                [&next],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(icon, "data:image/png;base64,AA");
        let source: String = db
            .query_row(
                "SELECT source_path FROM localization_entry_v2 WHERE package_path=?1",
                [&next],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(source, next);
        drop(db);
        f.run(Path::new(""), "restore").unwrap();
        let db = super::super::open_db(&f.db).unwrap();
        assert_eq!(
            db.query_row("SELECT path FROM mod_media_cache", [], |r| r
                .get::<_, String>(0))
                .unwrap(),
            old
        );
    }

    #[test]
    fn interrupted_switch_can_restore_original_and_reverse_cache_mapping() {
        let f = Fixture::new();
        let target = f.base.join("target");
        let backup = f.base.join(".ets2mm-original-interrupted");
        let journal = Journal {
            root: f.root.clone(),
            source: fs::canonicalize(&f.root).unwrap(),
            destination: target.clone(),
            backup: backup.clone(),
            staging: f.base.join(".ets2mm-copy-interrupted"),
            operation: "relocate".into(),
        };
        write_journal(&f.root, &journal).unwrap();
        copy_tree(&f.root, &target, &mut 0, 0, &mut |_| {}).unwrap();
        fs::rename(&f.root, &backup).unwrap();
        create_link(&target, &f.root).unwrap();
        remap_cache(&f.db, &journal.source, &target, false).unwrap();
        assert!(status(&f.root).unwrap().recovery_pending);
        assert!(read_lock(&f.root).is_err());
        recover(&f.root, &f.db).unwrap();
        assert_eq!(status(&f.root).unwrap().kind, "local");
        assert_eq!(
            fs::read(f.root.join("sample.scs")).unwrap(),
            b"sample archive"
        );
        assert!(target.join("sample.scs").is_file());
        assert!(!journal_path(&f.root).exists());
    }

    #[test]
    fn incremental_scan_after_relocation_reuses_all_package_signatures() {
        use super::super::{discover_packages, open_db, sync_index};
        use std::sync::atomic::AtomicBool;
        let f = Fixture::new();
        let mut db = open_db(&f.db).unwrap();
        let scan = || discover_packages(Some(&f.root), false, &AtomicBool::new(false));
        sync_index(&mut db, &scan()).unwrap();
        db.execute("UPDATE mod_package_v2 SET display_name='Persisted title', package_name='persisted-package'", []).unwrap();
        drop(db);
        f.run(&f.base.join("destination"), "relocate").unwrap();
        let mut db = open_db(&f.db).unwrap();
        let summary = sync_index(&mut db, &scan()).unwrap();
        assert_eq!(
            (
                summary.added,
                summary.updated,
                summary.removed,
                summary.inspected
            ),
            (0, 0, 0, 0)
        );
    }
}
