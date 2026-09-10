using ETS2ModManager.Contracts;
using Microsoft.Data.Sqlite;

namespace ETS2ModManager.Infrastructure.Indexing;

/// <summary>
/// Persists the raw localization components extracted from each package.
/// Resolution against baseline, dictionary, and UFL data is intentionally kept
/// out of this store so those sources can change without touching package files.
/// </summary>
public sealed class SqliteLocalizationIndex
{
    private readonly string _connectionString;

    public SqliteLocalizationIndex(string databasePath)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(databasePath) ?? ".");
        _connectionString = new SqliteConnectionStringBuilder
        {
            DataSource = databasePath,
            Mode = SqliteOpenMode.ReadWriteCreate,
            Cache = SqliteCacheMode.Shared,
        }.ToString();
        Initialize();
    }

    public bool TryLoad(
        string packagePath,
        string targetLocale,
        string packageType,
        long fileSize,
        long modifiedAtMs,
        int scanVersion,
        out IReadOnlyList<LocalizationEntry> entries)
    {
        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT file_size, modified_at_ms, package_type, scan_version
            FROM localization_package_snapshot
            WHERE package_path = $path AND target_locale = $locale;
            """;
        command.Parameters.AddWithValue("$path", packagePath);
        command.Parameters.AddWithValue("$locale", targetLocale);
        using var reader = command.ExecuteReader();
        if (!reader.Read()
            || reader.GetInt64(0) != fileSize
            || reader.GetInt64(1) != modifiedAtMs
            || !string.Equals(reader.GetString(2), packageType, StringComparison.OrdinalIgnoreCase)
            || reader.GetInt32(3) != scanVersion)
        {
            entries = [];
            return false;
        }

        reader.Close();
        using var entryCommand = connection.CreateCommand();
        entryCommand.CommandText = """
            SELECT entry_order, key, value, source_path, package_name, category,
                   status, locale_key_present, def_locale_key_present,
                   matched_key, unit_name, locale_key
            FROM localization_entry_snapshot
            WHERE package_path = $path AND target_locale = $locale
            ORDER BY entry_order;
            """;
        entryCommand.Parameters.AddWithValue("$path", packagePath);
        entryCommand.Parameters.AddWithValue("$locale", targetLocale);
        using var entryReader = entryCommand.ExecuteReader();
        var result = new List<LocalizationEntry>();
        while (entryReader.Read())
        {
            result.Add(new LocalizationEntry(
                entryReader.GetString(1),
                entryReader.GetString(2),
                entryReader.GetString(3),
                entryReader.GetString(4),
                entryReader.GetString(5),
                entryReader.GetString(6),
                entryReader.GetInt32(7) != 0,
                entryReader.GetInt32(8) != 0,
                entryReader.GetString(9),
                entryReader.GetString(10),
                entryReader.GetString(11)));
        }

        entries = result;
        return true;
    }

    public void Save(
        string packagePath,
        string targetLocale,
        string packageType,
        long fileSize,
        long modifiedAtMs,
        int scanVersion,
        IReadOnlyList<LocalizationEntry> entries)
    {
        using var connection = Open();
        using var transaction = connection.BeginTransaction();

        using (var deleteEntries = connection.CreateCommand())
        {
            deleteEntries.Transaction = transaction;
            deleteEntries.CommandText = "DELETE FROM localization_entry_snapshot WHERE package_path = $path AND target_locale = $locale";
            deleteEntries.Parameters.AddWithValue("$path", packagePath);
            deleteEntries.Parameters.AddWithValue("$locale", targetLocale);
            deleteEntries.ExecuteNonQuery();
        }

        using (var deletePackage = connection.CreateCommand())
        {
            deletePackage.Transaction = transaction;
            deletePackage.CommandText = "DELETE FROM localization_package_snapshot WHERE package_path = $path AND target_locale = $locale";
            deletePackage.Parameters.AddWithValue("$path", packagePath);
            deletePackage.Parameters.AddWithValue("$locale", targetLocale);
            deletePackage.ExecuteNonQuery();
        }

        using (var insertPackage = connection.CreateCommand())
        {
            insertPackage.Transaction = transaction;
            insertPackage.CommandText = """
                INSERT INTO localization_package_snapshot
                    (package_path, target_locale, package_type, file_size, modified_at_ms, scan_version, scanned_at_ms)
                VALUES ($path, $locale, $type, $size, $modified, $version, $scanned);
                """;
            insertPackage.Parameters.AddWithValue("$path", packagePath);
            insertPackage.Parameters.AddWithValue("$locale", targetLocale);
            insertPackage.Parameters.AddWithValue("$type", packageType);
            insertPackage.Parameters.AddWithValue("$size", fileSize);
            insertPackage.Parameters.AddWithValue("$modified", modifiedAtMs);
            insertPackage.Parameters.AddWithValue("$version", scanVersion);
            insertPackage.Parameters.AddWithValue("$scanned", DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
            insertPackage.ExecuteNonQuery();
        }

        for (var index = 0; index < entries.Count; index++)
        {
            var entry = entries[index];
            using var insertEntry = connection.CreateCommand();
            insertEntry.Transaction = transaction;
            insertEntry.CommandText = """
                INSERT INTO localization_entry_snapshot
                    (package_path, target_locale, entry_order, key, value, source_path,
                     package_name, category, status, locale_key_present, def_locale_key_present,
                     matched_key, unit_name, locale_key)
                VALUES ($path, $locale, $order, $key, $value, $source, $package,
                        $category, $status, $localePresent, $defLocalePresent,
                        $matched, $unit, $localeKey);
                """;
            insertEntry.Parameters.AddWithValue("$path", packagePath);
            insertEntry.Parameters.AddWithValue("$locale", targetLocale);
            insertEntry.Parameters.AddWithValue("$order", index);
            insertEntry.Parameters.AddWithValue("$key", entry.Key);
            insertEntry.Parameters.AddWithValue("$value", entry.Value);
            insertEntry.Parameters.AddWithValue("$source", entry.SourcePath);
            insertEntry.Parameters.AddWithValue("$package", entry.PackageName);
            insertEntry.Parameters.AddWithValue("$category", entry.Category);
            insertEntry.Parameters.AddWithValue("$status", entry.Status);
            insertEntry.Parameters.AddWithValue("$localePresent", entry.LocaleKeyPresent ? 1 : 0);
            insertEntry.Parameters.AddWithValue("$defLocalePresent", entry.DefLocaleKeyPresent ? 1 : 0);
            insertEntry.Parameters.AddWithValue("$matched", entry.MatchedKey);
            insertEntry.Parameters.AddWithValue("$unit", entry.UnitName);
            insertEntry.Parameters.AddWithValue("$localeKey", entry.LocaleKey);
            insertEntry.ExecuteNonQuery();
        }

        transaction.Commit();
    }

    public void RemoveExcept(string targetLocale, IReadOnlySet<string> packagePaths)
    {
        using var connection = Open();
        using var transaction = connection.BeginTransaction();
        using var command = connection.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = "SELECT package_path FROM localization_package_snapshot WHERE target_locale = $locale";
        command.Parameters.AddWithValue("$locale", targetLocale);
        using var reader = command.ExecuteReader();
        var stale = new List<string>();
        while (reader.Read())
        {
            var path = reader.GetString(0);
            if (!packagePaths.Contains(path)) stale.Add(path);
        }
        reader.Close();

        foreach (var path in stale)
        {
            using var deleteEntries = connection.CreateCommand();
            deleteEntries.Transaction = transaction;
            deleteEntries.CommandText = "DELETE FROM localization_entry_snapshot WHERE package_path = $path AND target_locale = $locale";
            deleteEntries.Parameters.AddWithValue("$path", path);
            deleteEntries.Parameters.AddWithValue("$locale", targetLocale);
            deleteEntries.ExecuteNonQuery();

            using var deletePackage = connection.CreateCommand();
            deletePackage.Transaction = transaction;
            deletePackage.CommandText = "DELETE FROM localization_package_snapshot WHERE package_path = $path AND target_locale = $locale";
            deletePackage.Parameters.AddWithValue("$path", path);
            deletePackage.Parameters.AddWithValue("$locale", targetLocale);
            deletePackage.ExecuteNonQuery();
        }
        transaction.Commit();
    }

    private SqliteConnection Open()
    {
        var connection = new SqliteConnection(_connectionString);
        connection.Open();
        return connection;
    }

    private void Initialize()
    {
        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS localization_package_snapshot(
                package_path TEXT NOT NULL,
                target_locale TEXT NOT NULL,
                package_type TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                modified_at_ms INTEGER NOT NULL,
                scan_version INTEGER NOT NULL,
                scanned_at_ms INTEGER NOT NULL,
                PRIMARY KEY(package_path, target_locale));
            CREATE TABLE IF NOT EXISTS localization_entry_snapshot(
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
                matched_key TEXT NOT NULL,
                unit_name TEXT NOT NULL,
                locale_key TEXT NOT NULL,
                PRIMARY KEY(package_path, target_locale, entry_order));
            CREATE INDEX IF NOT EXISTS ix_localization_entry_key
                ON localization_entry_snapshot(target_locale, key);
            """;
        command.ExecuteNonQuery();
    }
}
