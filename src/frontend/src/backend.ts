import { invoke } from "@tauri-apps/api/core";
import type { ModRecord, Profile } from "./types";

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
