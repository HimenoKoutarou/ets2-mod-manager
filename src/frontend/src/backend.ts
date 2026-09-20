import { invoke } from "@tauri-apps/api/core";
import type { ModRecord, Profile, SaveSlot } from "./types";

export interface ScanSummary {
  total: number;
  added: number;
  updated: number;
  removed: number;
  inspected: number;
  elapsedMs: number;
}

export interface ScanProgress {
  phase: "cache" | "local" | "workshop" | "metadata" | "cached" | "persist" | "media" | "complete";
  current: number;
  total: number;
  name: string;
  path: string;
}

export interface ModMedia {
  modId: string;
  iconUrl?: string;
  previewUrl?: string;
}

export interface CategorySnapshot {
  folders: string[];
  assignments: Record<string, string>;
  warning?: string;
}
export interface CategoryMutation {
  operation: "create" | "rename" | "delete" | "assign";
  name: string;
  newName?: string;
  modIds?: string[];
}

export interface PresetRecord {
  name: string;
  activeMods: string[];
}

export interface UpdateInfo {
  hasUpdate: boolean;
  latestVersion: string;
  currentVersion: string;
  releaseName: string;
  releaseNotes: string;
  assetName: string;
  assetSize: number;
  downloadUrl: string;
}

export interface UpdateDownload {
  path: string;
}

export interface LocalizationEntry {
  key: string;
  value: string;
  sourceName: string;
  sourcePath: string;
  packageName: string;
  category: string;
  status: string;
  localeKeyPresent: boolean;
  defLocaleKeyPresent: boolean;
  unitName: string;
  localeKey: string;
}

export interface LocalizationScan {
  entries: LocalizationEntry[];
  packages: number;
  inspected: number;
  cached: number;
  elapsedMs: number;
}

export interface CrashIssue {
  modId: string;
  displayName: string;
  severity: string;
  code: string;
  evidence: string;
  priorityIndex?: number;
}

export interface CrashPrecheck {
  profileId: string;
  scannedMods: number;
  redCount: number;
  yellowCount: number;
  issues: CrashIssue[];
  logPath?: string;
  crashPath?: string;
  logSummary?: string;
  logEvidence?: string[];
}

export interface BsiiSummary {
  version: number;
  definitions: number;
  objects: number;
}

export interface SaveField {
  structureName: string;
  fieldName: string;
  typeId: number;
  value: number;
  offset: number;
  size: number;
}

export interface SaveSnapshot {
  version: number;
  fields: SaveField[];
}

export interface SaveMutation {
  success: boolean;
  operation: string;
  message: string;
  backupPath?: string;
  value?: number;
}

export interface SaveObjectField {
  name: string;
  typeId: number;
  value: string;
  offset: number;
  size: number;
}

export interface SaveObject {
  objectIndex: number;
  structureName: string;
  kind: "truck" | "trailer" | "garage" | "city" | "dealer" | "skill" | "profile";
  fields: SaveObjectField[];
}

export interface SaveInventory {
  version: number;
  objects: SaveObject[];
  trucks: number;
  trailers: number;
  garages: number;
  cities: number;
  dealers: number;
  skills: number;
}

interface SaveSlotWire {
  profileId: string;
  slotId: string;
  folder: string;
  gameSii: string;
  displayName: string;
  lastModifiedMs: number;
  profileLocation: string;
}

export function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && (
    "__TAURI_INTERNALS__" in window || "__TAURI__" in window
  );
}

function sourceFor(packageType?: string): "local" | "workshop" {
  return packageType?.toLowerCase() === "workshop" ? "workshop" : "local";
}

function mapProfile(value: {
  id: string;
  name: string;
  company: string;
  location: string;
  folder?: string;
  modCount?: number;
  writable?: boolean;
}): Profile {
  return {
    id: value.id,
    name: value.name,
    company: value.company,
    location: value.location === "cloud" ? "cloud" : value.location === "steam" ? "steam" : "local",
    folder: value.folder,
    modCount: value.modCount ?? 0,
    writable: value.writable ?? value.location === "local",
  };
}

function mapMod(value: {
  id: string;
  packageName: string;
  path: string;
  packageType: string;
  displayName: string;
  author?: string;
  version?: string;
  size?: number;
  modifiedMs?: number;
  enabled?: boolean;
  category?: string;
}): ModRecord {
  return {
    id: value.id,
    packageName: value.packageName,
    path: value.path,
    packageType: value.packageType,
    displayName: value.displayName,
    author: value.author ?? "",
    version: value.version ?? "",
    source: sourceFor(value.packageType),
    category: value.category || "unknown",
    enabled: value.enabled ?? false,
    description: "",
    compatible: "",
    size: value.size,
    modifiedMs: value.modifiedMs,
  };
}

export interface ModBackend {
  readonly real: boolean;
  listCategories(): Promise<CategorySnapshot>;
  mutateCategories(request: CategoryMutation): Promise<CategorySnapshot>;
  initializeMods(): Promise<ScanSummary>;
  listProfiles(): Promise<Profile[]>;
  listMods(profileId: string): Promise<ModRecord[]>;
  loadModMedia(mods: ModRecord[]): Promise<ModMedia[]>;
  listSaves(profileId: string): Promise<SaveSlot[]>;
  scanLocalization(profileId: string, targetLocale: string, baseFile?: string | null): Promise<LocalizationScan>;
  getLocalizationBase(): Promise<string | null>;
  pickLocalizationBase(): Promise<string | null>;
  cancelLocalization(): Promise<void>;
  saveLocalizationAs(entries: LocalizationEntry[], suggestedName: string): Promise<string | null>;
  writeLocalizationBase(baseFile: string, entries: LocalizationEntry[]): Promise<void>;
  precheckCrash(profileId: string): Promise<CrashPrecheck>;
  inspectBsii(path: string): Promise<BsiiSummary>;
  readSaveSnapshot(path: string): Promise<SaveSnapshot>;
  readSaveInventory(path: string): Promise<SaveInventory>;
  mutateSave(path: string, operation: "set_money" | "set_experience" | "set_level", value: number): Promise<SaveMutation>;
  scan(): Promise<ScanSummary>;
  cancelScan(): Promise<void>;
  saveProfile(profileId: string, mods: ModRecord[]): Promise<void>;
  launchGame(): Promise<void>;
  openModLocation(mod: ModRecord): Promise<void>;
  openProfileLocation(profile: Profile): Promise<void>;
  listPresets(profileId: string): Promise<PresetRecord[]>;
  savePreset(profileId: string, name: string, mods: ModRecord[]): Promise<void>;
  loadPreset(profileId: string, name: string): Promise<string[]>;
  checkUpdate(): Promise<UpdateInfo>;
  downloadUpdate(url: string, filename: string): Promise<UpdateDownload>;
  installUpdate(path: string): Promise<void>;
}

const tauriBackend: ModBackend = {
  real: true,
  listCategories: () => invoke<CategorySnapshot>("category_list"),
  mutateCategories: (request) => invoke<CategorySnapshot>("category_mutate", { request }),
  initializeMods() {
    return invoke<ScanSummary>("mod_initialize");
  },
  async listProfiles() {
    const rows = await invoke<Array<Parameters<typeof mapProfile>[0]>>("profile_list");
    return rows.map(mapProfile);
  },
  async listMods(profileId) {
    const rows = await invoke<Array<Parameters<typeof mapMod>[0]>>("mod_list", { profileId });
    return rows.map(mapMod);
  },
  async loadModMedia(mods) {
    if (!mods.length) return [];
    return invoke<ModMedia[]>("mod_media_batch", {
      requests: mods.map((mod) => ({
        modId: mod.id,
        path: mod.path ?? "",
        packageType: mod.packageType ?? "",
      })),
    });
  },
  async listSaves(profileId) {
    const rows = await invoke<SaveSlotWire[]>("save_list_local", { profileId });
    return rows.map((row) => ({
      profileId: row.profileId,
      slotId: row.slotId,
      folder: row.folder,
      gameSii: row.gameSii,
      displayName: row.displayName,
      lastModifiedMs: row.lastModifiedMs,
      profileLocation: row.profileLocation === "local" ? "local" : "readonly",
    }));
  },
  scanLocalization(profileId, targetLocale, baseFile) {
    return invoke<LocalizationScan>("localization_scan", {
      request: { profileId, targetLocale, baseFile },
    });
  },
  getLocalizationBase() {
    return invoke<string | null>("localization_base_get");
  },
  pickLocalizationBase() {
    return invoke<string | null>("localization_base_pick");
  },
  saveLocalizationAs(entries, suggestedName) {
    return invoke<string | null>("localization_save_as", { request: { entries, suggestedName } });
  },
  cancelLocalization() {
    return invoke<void>("localization_cancel");
  },
  writeLocalizationBase(baseFile, entries) {
    return invoke<void>("localization_write_base", { request: { baseFile, entries } });
  },
  precheckCrash(profileId) {
    return invoke<CrashPrecheck>("crash_precheck", {
      request: { profileId },
    });
  },
  inspectBsii(path) {
    return invoke<BsiiSummary>("save_inspect_bsii", { request: { path } });
  },
  readSaveSnapshot(path) {
    return invoke<SaveSnapshot>("save_read_snapshot", { request: { path } });
  },
  readSaveInventory(path) {
    return invoke<SaveInventory>("save_read_inventory", { request: { path } });
  },
  mutateSave(path, operation, value) {
    return invoke<SaveMutation>("save_mutate", { request: { path, operation, value } });
  },
  scan() {
    return invoke<ScanSummary>("mod_scan");
  },
  cancelScan() {
    return invoke<void>("mod_cancel");
  },
  async saveProfile(profileId, mods) {
    const activeMods = mods.filter((mod) => mod.enabled).map((mod) => mod.packageName).reverse();
    await invoke("profile_write_active", { request: { profileId, activeMods } });
  },
  async launchGame() {
    await invoke("game_launch");
  },
  async openModLocation(mod) {
    await invoke("mod_open_location", {
      packageName: mod.packageName,
      path: mod.path ?? "",
      packageType: mod.packageType ?? mod.source,
    });
  },
  async openProfileLocation(profile) {
    await invoke("profile_open_location", { profileId: profile.id });
  },
  async listPresets(profileId) {
    return invoke<PresetRecord[]>("preset_list", { profileId });
  },
  async savePreset(profileId, name, mods) {
    const activeMods = mods.filter((mod) => mod.enabled).map((mod) => mod.packageName).reverse();
    await invoke("preset_save", { request: { profileId, name, activeMods } });
  },
  async loadPreset(profileId, name) {
    return invoke<string[]>("preset_load", { request: { profileId, name } });
  },
  checkUpdate() {
    return invoke<UpdateInfo>("check_update");
  },
  downloadUpdate(url, filename) {
    return invoke<UpdateDownload>("download_update", { request: { url, filename } });
  },
  installUpdate(path) {
    return invoke<void>("install_update", { request: { path } });
  },
};

export function createBackend(fixture: ModBackend): ModBackend {
  return isTauriRuntime() ? tauriBackend : fixture;
}

let indexInitialization: Promise<ScanSummary> | undefined;

export function initializeModIndex(): Promise<ScanSummary> {
  // StrictMode may remount effects while the native worker is still running.
  indexInitialization ??= (isTauriRuntime()
    ? invoke<ScanSummary>("mod_initialize")
    : Promise.resolve({ total: 0, added: 0, updated: 0, removed: 0, inspected: 0, elapsedMs: 0 })
  ).catch((error: unknown) => {
    indexInitialization = undefined;
    throw error;
  });
  return indexInitialization;
}
