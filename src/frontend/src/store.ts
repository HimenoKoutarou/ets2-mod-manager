import { create } from "zustand";
import type { Language, ModRecord, ModView, Profile } from "./types";

export const profiles: Profile[] = [
  { id: "profile-main", name: "长途运输", company: "Himeno Logistics", location: "local", modCount: 4 },
  { id: "profile-test", name: "地图测试", company: "Test Company", location: "local", modCount: 2 },
  { id: "profile-cloud", name: "Steam Cloud", company: "Remote", location: "cloud", modCount: 3 },
];

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

export interface ModBackend {
  listProfiles(): Profile[];
  listMods(profileId: string): ModRecord[];
  scan(): Promise<void>;
  saveProfile(profileId: string, mods: ModRecord[]): Promise<void>;
}

export const fixtureBackend: ModBackend = {
  listProfiles: () => profiles,
  listMods: () => initialMods.map((mod) => ({ ...mod })),
  scan: () => new Promise((resolve) => window.setTimeout(resolve, 900)),
  saveProfile: () => Promise.resolve(),
};

interface PresetSnapshot {
  id: string;
  enabled: boolean;
}

interface ModState {
  language: Language;
  selectedProfileId: string;
  mods: ModRecord[];
  view: ModView;
  query: string;
  selectedModId: string | null;
  dirty: boolean;
  scanning: boolean;
  presets: Record<string, PresetSnapshot[]>;
  selectedPresetName: string;
  setLanguage: (language: Language) => void;
  selectProfile: (id: string) => void;
  setView: (view: ModView) => void;
  setQuery: (query: string) => void;
  toggleMod: (id: string) => void;
  setAll: (enabled: boolean) => void;
  invertAll: () => void;
  selectMod: (id: string) => void;
  moveMod: (id: string, targetIndex: number) => void;
  scan: () => void;
  save: () => void;
  savePreset: (name: string) => void;
  selectPreset: (name: string) => void;
  loadPreset: () => void;
}

export const useModStore = create<ModState>((set) => ({
  language: "zh_CN",
  selectedProfileId: profiles[0].id,
  mods: fixtureBackend.listMods(profiles[0].id),
  view: "all",
  query: "",
  selectedModId: initialMods[0].id,
  dirty: false,
  scanning: false,
  presets: {},
  selectedPresetName: "",
  setLanguage: (language) => set({ language }),
  selectProfile: (selectedProfileId) => set({
    selectedProfileId,
    mods: fixtureBackend.listMods(selectedProfileId),
    selectedModId: null,
    dirty: false,
  }),
  setView: (view) => set({ view }),
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
  scan: () => {
    set({ scanning: true });
    void fixtureBackend.scan().finally(() => set({ scanning: false }));
  },
  save: () => set({ dirty: false }),
  savePreset: (name) =>
    set((state) => {
      const normalized = name.trim();
      if (!normalized) return state;
      return {
        presets: {
          ...state.presets,
          [normalized]: state.mods.map(({ id, enabled }) => ({ id, enabled })),
        },
        selectedPresetName: normalized,
      };
    }),
  selectPreset: (selectedPresetName) => set({ selectedPresetName }),
  loadPreset: () =>
    set((state) => {
      const snapshot = state.presets[state.selectedPresetName];
      if (!snapshot) return state;
      const order = new Map(snapshot.map((entry, index) => [entry.id, { ...entry, index }]));
      const mods = [...state.mods]
        .map((mod) => ({ mod, entry: order.get(mod.id) }))
        .sort((a, b) => (a.entry?.index ?? Number.MAX_SAFE_INTEGER) - (b.entry?.index ?? Number.MAX_SAFE_INTEGER))
        .map(({ mod, entry }) => entry ? { ...mod, enabled: entry.enabled } : mod);
      return { mods, dirty: true, selectedModId: mods[0]?.id ?? null };
    }),
}));
