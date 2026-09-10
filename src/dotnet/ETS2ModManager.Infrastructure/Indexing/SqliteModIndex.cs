using System.Text.Json;
using ETS2ModManager.Contracts;
using Microsoft.Data.Sqlite;

namespace ETS2ModManager.Infrastructure.Indexing;

public sealed class SqliteModIndex
{
    private readonly string _connectionString;
    public SqliteModIndex(string databasePath)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(databasePath) ?? ".");
        _connectionString = new SqliteConnectionStringBuilder { DataSource = databasePath, Mode = SqliteOpenMode.ReadWriteCreate, Cache = SqliteCacheMode.Shared }.ToString();
        Initialize();
    }

    public void Upsert(ModScanResult result)
    {
        using var connection = Open(); using var tx = connection.BeginTransaction();
        foreach (var mod in result.Mods) UpsertRow(connection, tx, mod);
        tx.Commit();
    }

    /// <summary>Replace the complete scan snapshot atomically.</summary>
    public void Replace(ModScanResult result)
    {
        using var connection = Open(); using var tx = connection.BeginTransaction();
        using (var clear = connection.CreateCommand())
        {
            clear.Transaction = tx;
            clear.CommandText = "DELETE FROM mod_package";
            clear.ExecuteNonQuery();
        }
        foreach (var mod in result.Mods) UpsertRow(connection, tx, mod);
        tx.Commit();
    }

    /// <summary>
    /// Synchronize only changed, added, and removed rows. Unchanged records
    /// keep their existing database row and scanned timestamp.
    /// </summary>
    public IndexSyncResult SyncSnapshot(ModScanResult result)
    {
        using var connection = Open();
        using var tx = connection.BeginTransaction();
        var existing = ReadRows(connection, tx);
        var incomingByPath = result.Mods
            .GroupBy(row => row.PackagePath, StringComparer.OrdinalIgnoreCase)
            .ToDictionary(group => group.Key, group => group.First(), StringComparer.OrdinalIgnoreCase);
        var removed = 0;
        var updated = 0;
        var added = 0;

        foreach (var row in existing)
        {
            if (!incomingByPath.ContainsKey(row.PackagePath))
            {
                DeletePath(connection, tx, row.PackagePath);
                removed++;
            }
        }

        foreach (var mod in incomingByPath.Values)
        {
            var prior = existing.FirstOrDefault(row => string.Equals(row.PackagePath, mod.PackagePath, StringComparison.OrdinalIgnoreCase));
            if (prior is not null && Equivalent(prior, mod)) continue;
            if (prior is not null) DeletePath(connection, tx, mod.PackagePath);
            UpsertRow(connection, tx, mod);
            if (prior is null) added++; else updated++;
        }

        tx.Commit();
        return new IndexSyncResult(added, updated, removed, incomingByPath.Count);
    }

    public IReadOnlyList<ModRecord> Query()
    {
        using var connection = Open(); using var cmd = connection.CreateCommand(); cmd.CommandText = "SELECT mod_id,package_name,package_path,package_type,display_name,file_size,last_modified_ms FROM mod_package ORDER BY display_name COLLATE NOCASE";
        using var reader = cmd.ExecuteReader(); var rows = new List<ModRecord>();
        while (reader.Read()) rows.Add(new ModRecord(reader.GetString(0), reader.GetString(1), reader.GetString(2), reader.GetString(3), reader.GetString(4), reader.GetInt64(5), reader.GetInt64(6)));
        return rows;
    }

    private SqliteConnection Open() { var c = new SqliteConnection(_connectionString); c.Open(); return c; }
    private static IReadOnlyList<ModRecord> ReadRows(SqliteConnection connection, SqliteTransaction tx)
    {
        using var cmd = connection.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = "SELECT mod_id,package_name,package_path,package_type,display_name,file_size,last_modified_ms FROM mod_package";
        using var reader = cmd.ExecuteReader();
        var rows = new List<ModRecord>();
        while (reader.Read()) rows.Add(new ModRecord(reader.GetString(0), reader.GetString(1), reader.GetString(2), reader.GetString(3), reader.GetString(4), reader.GetInt64(5), reader.GetInt64(6)));
        return rows;
    }

    private static void DeletePath(SqliteConnection connection, SqliteTransaction tx, string path)
    {
        using var cmd = connection.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = "DELETE FROM mod_package WHERE package_path = $path";
        cmd.Parameters.AddWithValue("$path", path);
        cmd.ExecuteNonQuery();
    }

    private static bool Equivalent(ModRecord left, ModRecord right) =>
        string.Equals(left.ModId, right.ModId, StringComparison.Ordinal)
        && string.Equals(left.PackageName, right.PackageName, StringComparison.Ordinal)
        && string.Equals(left.PackagePath, right.PackagePath, StringComparison.OrdinalIgnoreCase)
        && string.Equals(left.PackageType, right.PackageType, StringComparison.OrdinalIgnoreCase)
        && string.Equals(left.DisplayName, right.DisplayName, StringComparison.Ordinal)
        && left.FileSize == right.FileSize
        && left.LastModifiedUnixMilliseconds == right.LastModifiedUnixMilliseconds;

    private static void UpsertRow(SqliteConnection connection, SqliteTransaction tx, ModRecord mod)
    {
        using var cmd = connection.CreateCommand(); cmd.Transaction = tx;
        cmd.CommandText = "INSERT INTO mod_package(identity_key,mod_id,package_name,package_path,package_type,display_name,file_size,last_modified_ms,scanned_at_ms) VALUES($key,$id,$name,$path,$type,$display,$size,$modified,$scanned) ON CONFLICT(identity_key) DO UPDATE SET mod_id=excluded.mod_id,package_name=excluded.package_name,package_path=excluded.package_path,package_type=excluded.package_type,display_name=excluded.display_name,file_size=excluded.file_size,last_modified_ms=excluded.last_modified_ms,scanned_at_ms=excluded.scanned_at_ms";
        cmd.Parameters.AddWithValue("$key", mod.ModId + "|" + mod.PackagePath); cmd.Parameters.AddWithValue("$id", mod.ModId); cmd.Parameters.AddWithValue("$name", mod.PackageName); cmd.Parameters.AddWithValue("$path", mod.PackagePath); cmd.Parameters.AddWithValue("$type", mod.PackageType); cmd.Parameters.AddWithValue("$display", mod.DisplayName); cmd.Parameters.AddWithValue("$size", mod.FileSize); cmd.Parameters.AddWithValue("$modified", mod.LastModifiedUnixMilliseconds); cmd.Parameters.AddWithValue("$scanned", DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()); cmd.ExecuteNonQuery();
    }
    private void Initialize()
    {
        using var c = Open(); using var cmd = c.CreateCommand(); cmd.CommandText = "PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;" + "CREATE TABLE IF NOT EXISTS mod_package(identity_key TEXT PRIMARY KEY, mod_id TEXT NOT NULL, package_name TEXT NOT NULL, package_path TEXT NOT NULL, package_type TEXT NOT NULL, display_name TEXT NOT NULL, file_size INTEGER NOT NULL, last_modified_ms INTEGER NOT NULL, content_hash BLOB, scanned_at_ms INTEGER NOT NULL);" + "CREATE INDEX IF NOT EXISTS ix_mod_package_display_name ON mod_package(display_name COLLATE NOCASE);" + "CREATE TABLE IF NOT EXISTS profile_snapshot(profile_id TEXT NOT NULL, location TEXT NOT NULL, profile_sii TEXT NOT NULL, modified_at_ms INTEGER NOT NULL, active_mods_json TEXT NOT NULL, PRIMARY KEY(profile_id, location));"; cmd.ExecuteNonQuery();
    }
}

public sealed record IndexSyncResult(int Added, int Updated, int Removed, int Total);
