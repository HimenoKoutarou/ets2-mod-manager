import { create } from "zustand";
import { createBackend, type ModBackend, type PresetRecord } from "./backend";
import type { Language, ModRecord, ModView, Profile } from "./types";

export const fixtureProfiles: Profile[] = [
  { id: "profile-main", name: "长途运输", company: "Himeno Logistics", location: "local", modCount: 4, writable: true },
  { id: "profile-test", name: "地图测试", company: "Test Company", location: "local", modCount: 2, writable: true },
  { id: "profile-cloud", name: "Steam Cloud", company: "Remote", location: "cloud", modCount: 3, writable: false },
];
export const profiles = fixtureProfiles;

export const initialMods: ModRecord[] = [
  {
    id: "promods-europe",
    packageName: "promods-europe",
    displayName: "ProMods Europe",
    author: "ProMods Team",
    version: "2.72",
    source: "local",
    category: "地图",
    enabled: true,
    description: "Expanded Europe map with new cities, roads and detailed scenery.",
    compatible: "1.57.x",
  },
  {
    id: "real-company",
    packageName: "real-company-logo",
    displayName: "Real Company Logo",
    author: "DreamTeam",
    version: "1.9.4",
    source: "workshop",
    category: "图形",
    enabled: true,
    description: "Real-world company logos for trailers and depots.",
    compatible: "1.57.x",
  },
  {
    id: "dreamlike-weather",
    packageName: "dreamlike-weather",
    displayName: "Dreamlike Weather",
    author: "Marek",
    version: "4.1",
    source: "local",
    category: "天气",
    enabled: true,
    description: "A restrained weather and lighting overhaul for long-haul routes.",
    compatible: "1.56.x / 1.57.x",
  },
  {
    id: "traffic-density",
    packageName: "traffic-density-extended",
    displayName: "Traffic Density Extended",
    author: "TrafficLab",
    version: "3.0",
    source: "workshop",
    category: "AI 交通",
    enabled: false,
    description: "More varied traffic patterns and configurable density profiles.",
    compatible: "1.57.x",
  },
  {
    id: "sound-fix",
    packageName: "sound-fix-pack",
    displayName: "Sound Fixes Pack",
    author: "Drive Safely",
    version: "24.12",
    source: "local",
    category: "声音",
    enabled: false,
    description: "Sound improvements and fixes for trucks, environments and UI.",
    compatible: "1.57.x",
  },
];

export const fixtureBackend: ModBackend = {
  real: false,
  listProfiles: async () => fixtureProfiles.map((profile) => ({ ...profile })),
  listMods: async () => initialMods.map((mod) => ({ ...mod })),
  scan: async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 350));
    return { total: initialMods.length, added: 0, updated: 0, removed: 0, inspected: 0, elapsedMs: 350 };
  },
  saveProfile: async () => undefined,
  launchGame: async () => undefined,
  listPresets: async () => [],
  savePreset: async () => undefined,
  loadPreset: async () => [],
};

const backend = createBackend(fixtureBackend);

interface PresetSnapshot {
  id: string;
  enabled: boolean;
}

function snapshotFromPreset(preset: PresetRecord, mods: ModRecord[]): PresetSnapshot[] {
  const indexByPackage = new Map(mods.map((mod) => [mod.packageName.toLocaleLowerCase(), mod.id]));
  return preset.activeMods
    .map((packageName) => indexByPackage.get(packageName.toLocaleLowerCase()))
    .filter((id): id is string => Boolean(id))
    .map((id) => ({ id, enabled: true }));
}

function reorderWithActive(mods: ModRecord[], activeIds: string[]): ModRecord[] {
  const rank = new Map(activeIds.map((id, index) => [id, index]));
  return [...mods].sort((left, right) => {
    const leftRank = rank.get(left.id);
    const rightRank = rank.get(right.id);
    if (leftRank !== undefined && rightRank !== undefined) return leftRank - rightRank;
    if (leftRank !== undefined) return -1;
    if (rightRank !== undefined) return 1;
    return left.displayName.localeCompare(right.displayName);
  });
}

interface ModState {
  language: Language;
  profiles: Profile[];
  selectedProfileId: string;
  mods: ModRecord[];
  view: ModView;
  selectedCategory: string;
  query: string;
  selectedModId: string | null;
  dirty: boolean;
  scanning: boolean;
  loading: boolean;
  error: string;
  presets: Record<string, PresetSnapshot[]>;
  selectedPresetName: string;
  initialize: () => Promise<void>;
  setLanguage: (language: Language) => void;
  selectProfile: (id: string) => Promise<void>;
  setView: (view: ModView) => void;
  setCategory: (category: string) => void;
  setQuery: (query: string) => void;
  toggleMod: (id: string) => void;
  setAll: (enabled: boolean) => void;
  invertAll: () => void;
  selectMod: (id: string) => void;
  moveMod: (id: string, targetIndex: number) => void;
  scan: () => Promise<void>;
  save: () => Promise<void>;
  launch: () => Promise<void>;
  savePreset: (name: string) => Promise<void>;
  selectPreset: (name: string) => void;
  loadPreset: () => Promise<void>;
}

export const useModStore = create<ModState>((set, get) => ({
  language: "zh_CN",
  profiles: fixtureProfiles,
  selectedProfileId: fixtureProfiles[0]?.id ?? "",
  mods: initialMods.map((mod) => ({ ...mod })),
  view: "all",
  selectedCategory: "all",
  query: "",
  selectedModId: initialMods[0]?.id ?? null,
  dirty: false,
  scanning: false,
  loading: false,
  error: "",
  presets: {},
  selectedPresetName: "",
  initialize: async () => {
    set({ loading: true, error: "" });
    try {
      const profiles = await backend.listProfiles();
      const selectedProfileId = profiles.some((profile) => profile.id === get().selectedProfileId)
        ? get().selectedProfileId
        : profiles[0]?.id ?? "";
      const mods = selectedProfileId ? await backend.listMods(selectedProfileId) : [];
      const presets = selectedProfileId ? await backend.listPresets(selectedProfileId) : [];
      set({
        profiles,
        selectedProfileId,
        mods,
        selectedModId: mods[0]?.id ?? null,
        presets: Object.fromEntries(presets.map((preset) => [preset.name, snapshotFromPreset(preset, mods)])),
        selectedPresetName: presets[0]?.name ?? "",
        dirty: false,
      });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    } finally {
      set({ loading: false });
    }
  },
  setLanguage: (language) => set({ language }),
  selectProfile: async (selectedProfileId) => {
    set({ selectedProfileId, loading: true, error: "" });
    try {
      const mods = await backend.listMods(selectedProfileId);
      const presets = await backend.listPresets(selectedProfileId);
      set({
        mods,
        selectedModId: mods[0]?.id ?? null,
        presets: Object.fromEntries(presets.map((preset) => [preset.name, snapshotFromPreset(preset, mods)])),
        selectedPresetName: presets[0]?.name ?? "",
        dirty: false,
      });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    } finally {
      set({ loading: false });
    }
  },
  setView: (view) => set({ view }),
  setCategory: (selectedCategory) => set({ selectedCategory }),
  setQuery: (query) => set({ query }),
  toggleMod: (id) =>
    set((state) => ({
      mods: state.mods.map((mod) => (mod.id === id ? { ...mod, enabled: !mod.enabled } : mod)),
      dirty: true,
    })),
  setAll: (enabled) => set((state) => ({ mods: state.mods.map((mod) => ({ ...mod, enabled })), dirty: true })),
  invertAll: () => set((state) => ({ mods: state.mods.map((mod) => ({ ...mod, enabled: !mod.enabled })), dirty: true })),
  selectMod: (selectedModId) => set({ selectedModId }),
  moveMod: (id, targetIndex) =>
    set((state) => {
      const from = state.mods.findIndex((mod) => mod.id === id);
      if (from < 0 || targetIndex < 0 || targetIndex >= state.mods.length || from === targetIndex) return state;
      const mods = [...state.mods];
      const [moved] = mods.splice(from, 1);
      mods.splice(targetIndex, 0, moved);
      return { mods, selectedModId: id, dirty: true };
    }),
  scan: async () => {
    if (get().scanning) return;
    set({ scanning: true, error: "" });
    try {
      await backend.scan();
      const profileId = get().selectedProfileId;
      if (profileId) {
        const mods = await backend.listMods(profileId);
        set({ mods, selectedModId: mods[0]?.id ?? null, dirty: false });
      }
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    } finally {
      set({ scanning: false });
    }
  },
  save: async () => {
    const { selectedProfileId, mods } = get();
    if (!selectedProfileId) return;
    set({ loading: true, error: "" });
    try {
      await backend.saveProfile(selectedProfileId, mods);
      set({ dirty: false });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    } finally {
      set({ loading: false });
    }
  },
  launch: async () => {
    set({ error: "" });
    try {
      await backend.launchGame();
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    }
  },
  savePreset: async (name) => {
    const normalized = name.trim();
    const { selectedProfileId, mods } = get();
    if (!normalized || !selectedProfileId) return;
    set({ error: "" });
    try {
      await backend.savePreset(selectedProfileId, normalized, mods);
      set((state) => ({
        presets: { ...state.presets, [normalized]: state.mods.map(({ id, enabled }) => ({ id, enabled })) },
        selectedPresetName: normalized,
      }));
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    }
  },
  selectPreset: (selectedPresetName) => set({ selectedPresetName }),
  loadPreset: async () => {
    const { selectedProfileId, selectedPresetName, mods, presets } = get();
    if (!selectedProfileId || !selectedPresetName) return;
    try {
      const activePackages = backend.real
        ? (await backend.loadPreset(selectedProfileId, selectedPresetName)).reverse()
        : (presets[selectedPresetName] ?? [])
            .map((entry) => mods.find((mod) => mod.id === entry.id)?.packageName)
            .filter((value): value is string => Boolean(value));
      const activeIds = new Set(
        activePackages
          .map((packageName) => mods.find((mod) => mod.packageName.toLocaleLowerCase() === packageName.toLocaleLowerCase())?.id)
          .filter((id): id is string => Boolean(id)),
      );
      const orderedIds = activePackages
        .map((packageName) => mods.find((mod) => mod.packageName.toLocaleLowerCase() === packageName.toLocaleLowerCase())?.id)
        .filter((id): id is string => Boolean(id));
      const nextMods = reorderWithActive(mods.map((mod) => ({ ...mod, enabled: activeIds.has(mod.id) })), orderedIds);
      set({ mods: nextMods, dirty: true, selectedModId: nextMods[0]?.id ?? null });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    }
  },
}));
