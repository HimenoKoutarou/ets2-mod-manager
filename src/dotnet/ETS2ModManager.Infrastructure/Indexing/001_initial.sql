PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS mod_package (
    identity_key TEXT PRIMARY KEY,
    mod_id TEXT NOT NULL,
    package_name TEXT NOT NULL,
    package_path TEXT NOT NULL,
    package_type TEXT NOT NULL,
    display_name TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    last_modified_ms INTEGER NOT NULL,
    content_hash BLOB,
    scanned_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_mod_package_display_name
    ON mod_package(display_name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS profile_snapshot (
    profile_id TEXT NOT NULL,
    location TEXT NOT NULL,
    profile_sii TEXT NOT NULL,
    modified_at_ms INTEGER NOT NULL,
    active_mods_json TEXT NOT NULL,
    PRIMARY KEY(profile_id, location)
);
