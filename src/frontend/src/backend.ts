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

export interface PresetRecord {
  name: string;
  activeMods: string[];
}

export interface LocalizationEntry {
  key: string;
  value: string;
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
}

export interface BsiiSummary {
  version: number;
  definitions: number;
  objects: number;
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
  listProfiles(): Promise<Profile[]>;
  listMods(profileId: string): Promise<ModRecord[]>;
  listSaves(profileId: string): Promise<SaveSlot[]>;
  scanLocalization(profileId: string, targetLocale: string): Promise<LocalizationScan>;
  precheckCrash(profileId: string): Promise<CrashPrecheck>;
  inspectBsii(path: string): Promise<BsiiSummary>;
  scan(): Promise<ScanSummary>;
  saveProfile(profileId: string, mods: ModRecord[]): Promise<void>;
  launchGame(): Promise<void>;
  listPresets(profileId: string): Promise<PresetRecord[]>;
  savePreset(profileId: string, name: string, mods: ModRecord[]): Promise<void>;
  loadPreset(profileId: string, name: string): Promise<string[]>;
}

const tauriBackend: ModBackend = {
  real: true,
  async listProfiles() {
    const rows = await invoke<Array<Parameters<typeof mapProfile>[0]>>("profile_list");
    return rows.map(mapProfile);
  },
  async listMods(profileId) {
    const rows = await invoke<Array<Parameters<typeof mapMod>[0]>>("mod_list", { profileId });
    return rows.map(mapMod);
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
  scanLocalization(profileId, targetLocale) {
    return invoke<LocalizationScan>("localization_scan", {
      request: { profileId, targetLocale },
    });
  },
  precheckCrash(profileId) {
    return invoke<CrashPrecheck>("crash_precheck", {
      request: { profileId },
    });
  },
  inspectBsii(path) {
    return invoke<BsiiSummary>("save_inspect_bsii", { request: { path } });
  },
  scan() {
    return invoke<ScanSummary>("mod_scan");
  },
  async saveProfile(profileId, mods) {
    const activeMods = mods.filter((mod) => mod.enabled).map((mod) => mod.packageName).reverse();
    await invoke("profile_write_active", { request: { profileId, activeMods } });
  },
  async launchGame() {
    await invoke("game_launch");
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
};

export function createBackend(fixture: ModBackend): ModBackend {
  return isTauriRuntime() ? tauriBackend : fixture;
}
