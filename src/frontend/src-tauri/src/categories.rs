use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use std::{collections::HashMap, fs, path::Path};

type Result<T> = std::result::Result<T, String>;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Snapshot {
    pub folders: Vec<String>,
    pub assignments: HashMap<String, String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub warning: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Mutation {
    pub operation: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub new_name: String,
    #[serde(default)]
    pub mod_ids: Vec<String>,
}

fn err(e: impl std::fmt::Display) -> String {
    e.to_string()
}

fn identity(value: &str) -> String {
    let key = value
        .split('|')
        .next()
        .unwrap_or(value)
        .trim()
        .to_lowercase();
    if let Some(hex) = key.strip_prefix("mod_workshop_package.") {
        if let Ok(id) = u64::from_str_radix(hex, 16) {
            return id.to_string();
        }
    }
    key.strip_suffix(".scs")
        .or_else(|| key.strip_suffix(".zip"))
        .unwrap_or(&key)
        .into()
}

pub fn schema(db: &Connection) -> Result<()> {
    db.execute_batch(
        "CREATE TABLE IF NOT EXISTS category_folder (name TEXT PRIMARY KEY COLLATE NOCASE);
         CREATE TABLE IF NOT EXISTS category_assignment (mod_key TEXT PRIMARY KEY, category TEXT NOT NULL);
         CREATE TABLE IF NOT EXISTS category_import (source TEXT PRIMARY KEY);",
    ).map_err(err)
}

fn valid_name(value: &str) -> Result<String> {
    let value = value.trim();
    if value.is_empty() || value.chars().count() > 80 || value.chars().any(char::is_control) {
        return Err("category_invalid_name".into());
    }
    Ok(value.into())
}

pub fn snapshot(db: &Connection) -> Result<Snapshot> {
    schema(db)?;
    let folders = db
        .prepare("SELECT name FROM category_folder ORDER BY rowid")
        .map_err(err)?
        .query_map([], |row| row.get::<_, String>(0))
        .map_err(err)?
        .collect::<std::result::Result<Vec<_>, _>>()
        .map_err(err)?;
    let known = db
        .prepare("SELECT mod_key, category FROM category_assignment")
        .map_err(err)?
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })
        .map_err(err)?
        .collect::<std::result::Result<HashMap<_, _>, _>>()
        .map_err(err)?;
    // Stable identities survive relocation, rescans, and temporarily missing Mods.
    let mut assignments = known.clone();
    for row in super::load_cached(db)? {
        let category = known
            .get(&identity(&row.id))
            .or_else(|| known.get(&identity(&row.package_name)))
            .cloned()
            .unwrap_or_default();
        assignments.insert(row.id, category);
    }
    Ok(Snapshot {
        folders,
        assignments,
        warning: None,
    })
}

pub fn mutate(db: &mut Connection, request: Mutation) -> Result<Snapshot> {
    schema(db)?;
    let tx = db.transaction().map_err(err)?;
    match request.operation.as_str() {
        "create" => {
            let name = valid_name(&request.name)?;
            let inserted = tx
                .execute(
                    "INSERT OR IGNORE INTO category_folder(name) VALUES (?1)",
                    [&name],
                )
                .map_err(err)?;
            if inserted == 0 {
                return Err("category_exists".into());
            }
        }
        "rename" | "delete" => {
            let old = tx
                .query_row(
                    "SELECT name FROM category_folder WHERE name=?1",
                    [&request.name],
                    |r| r.get::<_, String>(0),
                )
                .map_err(|_| "category_missing".to_string())?;
            if request.operation == "rename" {
                let name = valid_name(&request.new_name)?;
                let duplicate: bool = tx
                    .query_row(
                        "SELECT EXISTS(SELECT 1 FROM category_folder WHERE name=?1 AND name<>?2)",
                        params![name, old],
                        |r| r.get(0),
                    )
                    .map_err(err)?;
                if duplicate {
                    return Err("category_exists".into());
                }
                tx.execute(
                    "UPDATE category_folder SET name=?1 WHERE name=?2",
                    params![name, old],
                )
                .map_err(err)?;
                tx.execute(
                    "UPDATE category_assignment SET category=?1 WHERE category=?2",
                    params![name, old],
                )
                .map_err(err)?;
            } else {
                tx.execute("DELETE FROM category_folder WHERE name=?1", [&old])
                    .map_err(err)?;
                tx.execute(
                    "UPDATE category_assignment SET category='' WHERE category=?1",
                    [&old],
                )
                .map_err(err)?;
            }
        }
        "assign" => {
            let category = if request.name.is_empty() {
                String::new()
            } else {
                tx.query_row(
                    "SELECT name FROM category_folder WHERE name=?1",
                    [&request.name],
                    |r| r.get::<_, String>(0),
                )
                .map_err(|_| "category_missing".to_string())?
            };
            let mods = super::load_cached(&tx)?;
            let identities: HashMap<_, _> = mods
                .iter()
                .map(|row| (row.id.as_str(), identity(&row.id)))
                .collect();
            for id in &request.mod_ids {
                let key = identities.get(id.as_str()).ok_or("category_mod_missing")?;
                tx.execute("INSERT INTO category_assignment VALUES (?1,?2) ON CONFLICT(mod_key) DO UPDATE SET category=excluded.category",
                    params![key, category]).map_err(err)?;
            }
        }
        _ => return Err("category_invalid_operation".into()),
    }
    tx.commit().map_err(err)?;
    snapshot(db)
}

fn read_json(path: &Path) -> Result<serde_json::Value> {
    if !path.exists() {
        return Ok(serde_json::Value::Null);
    }
    let bytes = fs::read(path).map_err(err)?;
    serde_json::from_slice(&bytes).map_err(|e| format!("{}: {e}", path.display()))
}

pub fn import_legacy(
    db: &mut Connection,
    legacy: Option<&Path>,
    dotnet: Option<&Path>,
) -> Result<()> {
    schema(db)?;
    let imported: bool = db
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM category_import WHERE source='legacy-v1')",
            [],
            |r| r.get(0),
        )
        .map_err(err)?;
    if imported {
        return Ok(());
    }
    let mut folders = Vec::new();
    let mut records = HashMap::new();
    if let Some(legacy) = legacy {
        if let Some(values) = read_json(&legacy.join("user_folders.json"))?.as_array() {
            folders.extend(values.iter().filter_map(|v| v.as_str().map(str::to_owned)));
        }
        if let Some(values) = read_json(&legacy.join("known_mods.json"))?.as_object() {
            for (key, record) in values {
                if let Some(category) = record.get("category").and_then(|v| v.as_str()) {
                    records.insert(identity(key), category.to_string());
                }
            }
        }
    }
    if let Some(path) = dotnet {
        let data = read_json(path)?;
        if let Some(values) = data.get("Folders").and_then(|v| v.as_array()) {
            folders.extend(values.iter().filter_map(|v| v.as_str().map(str::to_owned)));
        }
        if let Some(values) = data.get("Records").and_then(|v| v.as_array()) {
            for record in values {
                if let (Some(key), Some(category)) = (
                    record.get("Key").and_then(|v| v.as_str()),
                    record
                        .get("Value")
                        .and_then(|v| v.get("Category"))
                        .and_then(|v| v.as_str()),
                ) {
                    records.insert(identity(key), category.into());
                }
            }
        }
    }
    let tx = db.transaction().map_err(err)?;
    let imported: bool = tx
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM category_import WHERE source='legacy-v1')",
            [],
            |r| r.get(0),
        )
        .map_err(err)?;
    if imported {
        return Ok(());
    }
    for name in folders {
        if let Ok(name) = valid_name(&name) {
            tx.execute("INSERT OR IGNORE INTO category_folder VALUES (?1)", [name])
                .map_err(err)?;
        }
    }
    for (key, category) in records {
        // Deleted legacy folders remain uncategorized.
        tx.execute("INSERT OR IGNORE INTO category_assignment SELECT ?1,name FROM category_folder WHERE name=?2",
            params![key, category.trim()]).map_err(err)?;
    }
    tx.execute("INSERT INTO category_import VALUES ('legacy-v1')", [])
        .map_err(err)?;
    tx.commit().map_err(err)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn db() -> Connection {
        let db = Connection::open_in_memory().unwrap();
        super::super::open_db_schema(&db).unwrap();
        db.execute("INSERT INTO mod_package_v2 VALUES ('C:\\mods\\one.scs','one','one','scs','One','', '',1,1,1,1)", []).unwrap();
        db.execute("INSERT INTO mod_package_v2 VALUES ('D:\\workshop\\123','123','workshop.123','workshop','Two','', '',1,1,1,1)", []).unwrap();
        db
    }
    fn request(op: &str, name: &str, new: &str, ids: &[&str]) -> Mutation {
        Mutation {
            operation: op.into(),
            name: name.into(),
            new_name: new.into(),
            mod_ids: ids.iter().map(|s| s.to_string()).collect(),
        }
    }
    #[test]
    fn lifecycle_is_atomic_and_independent_of_scan_paths() {
        let mut db = db();
        mutate(&mut db, request("create", "地图", "", &[])).unwrap();
        mutate(&mut db, request("assign", "地图", "", &["one", "123"])).unwrap();
        assert_eq!(snapshot(&db).unwrap().assignments["one"], "地图");
        assert!(mutate(&mut db, request("assign", "", "", &["one", "missing"])).is_err());
        assert_eq!(snapshot(&db).unwrap().assignments["one"], "地图");
        db.execute(
            "UPDATE mod_package_v2 SET path='E:\\new\\one.scs' WHERE mod_id='one'",
            [],
        )
        .unwrap();
        assert_eq!(snapshot(&db).unwrap().assignments["one"], "地图");
        mutate(&mut db, request("rename", "地图", "Maps", &[])).unwrap();
        assert_eq!(snapshot(&db).unwrap().assignments["123"], "Maps");
        db.execute("DELETE FROM mod_package_v2 WHERE mod_id='one'", [])
            .unwrap();
        assert_eq!(snapshot(&db).unwrap().assignments["one"], "Maps");
        mutate(&mut db, request("delete", "Maps", "", &[])).unwrap();
        assert_eq!(snapshot(&db).unwrap().assignments["one"], "");
        assert!(snapshot(&db).unwrap().folders.is_empty());
    }
    #[test]
    fn rejects_invalid_and_duplicate_names() {
        let mut db = db();
        for name in ["", "  ", "\n", "bad\nname"] {
            assert!(mutate(&mut db, request("create", name, "", &[])).is_err());
        }
        mutate(&mut db, request("create", "Maps", "", &[])).unwrap();
        assert!(mutate(&mut db, request("create", "maps", "", &[])).is_err());
        mutate(&mut db, request("create", "Other", "", &[])).unwrap();
        assert!(mutate(&mut db, request("rename", "Maps", "Other", &[])).is_err());
        assert_eq!(snapshot(&db).unwrap().folders, ["Maps", "Other"]);
        assert!(mutate(&mut db, request("assign", "unknown", "", &["one"])).is_err());
    }
    #[test]
    fn imports_once_without_reviving_deleted_folders() {
        let root =
            std::env::temp_dir().join(format!("ets2-category-import-{}", super::super::now_ms()));
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("user_folders.json"), r#"["Maps"]"#).unwrap();
        fs::write(
            root.join("known_mods.json"),
            r#"{"one.scs":{"category":"Maps"},"123":{"category":"Deleted"}}"#,
        )
        .unwrap();
        let dotnet = root.join("categories.json");
        fs::write(
            &dotnet,
            r#"{"Folders":["Trucks"],"Records":[{"Key":"123","Value":{"Category":"Trucks"}}]}"#,
        )
        .unwrap();
        let mut db = db();
        import_legacy(&mut db, Some(&root), Some(&dotnet)).unwrap();
        assert_eq!(snapshot(&db).unwrap().assignments["one"], "Maps");
        assert_eq!(snapshot(&db).unwrap().assignments["123"], "Trucks");
        mutate(&mut db, request("delete", "Maps", "", &[])).unwrap();
        import_legacy(&mut db, Some(&root), Some(&dotnet)).unwrap();
        assert_eq!(snapshot(&db).unwrap().folders, ["Trucks"]);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn identity_preserves_copies_and_handles_legacy_workshop_names() {
        assert_eq!(identity("One.scs"), "one");
        assert_eq!(identity("One_copy.scs"), "one_copy");
        assert_ne!(identity("one"), identity("one_copy"));
        assert_eq!(identity("mod_workshop_package.7b"), "123");
    }

    #[test]
    fn folders_and_assignments_survive_database_reopen() {
        let root =
            std::env::temp_dir().join(format!("ets2-category-persist-{}", super::super::now_ms()));
        let path = root.join("index.db");
        let mut db = super::super::open_db(&path).unwrap();
        db.execute("INSERT INTO mod_package_v2 VALUES ('C:\\one.scs','one','one','scs','One','', '',1,1,1,1)", []).unwrap();
        mutate(&mut db, request("create", "地图", "", &[])).unwrap();
        mutate(&mut db, request("assign", "地图", "", &["one"])).unwrap();
        drop(db);
        let db = super::super::open_db(&path).unwrap();
        assert_eq!(snapshot(&db).unwrap().folders, ["地图"]);
        assert_eq!(snapshot(&db).unwrap().assignments["one"], "地图");
        drop(db);
        fs::remove_dir_all(root).unwrap();
    }
}
