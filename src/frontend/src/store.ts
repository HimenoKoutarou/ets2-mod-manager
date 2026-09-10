import { create } from "zustand";
import { createBackend, type CrashPrecheck, type LocalizationScan, type ModBackend, type PresetRecord, type SaveSnapshot, type ScanSummary } from "./backend";
import { getCopy } from "./i18n";
import type { Language, ModRecord, ModView, Profile, SaveSlot } from "./types";

export const fixtureProfiles: Profile[] = [
  { id: "profile-main", name: "长途运输", company: "Himeno Logistics", location: "local", modCount: 4, writable: true },
  { id: "profile-test", name: "地图测试", company: "Test Company", location: "local", modCount: 2, writable: true },
  { id: "profile-cloud", name: "Steam Cloud", company: "Remote", location: "cloud", modCount: 3, writable: false },
];
export const profiles = fixtureProfiles;
let fixtureSaveMoney = 1_253_729;
let fixtureSaveExperience = 279_375;

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
  listSaves: async (profileId) =>
    fixtureProfiles.find((profile) => profile.id === profileId)?.location === "local"
      ? [
          {
            profileId,
            slotId: "career-1",
            folder: `fixture://${profileId}/save/career-1`,
            gameSii: `fixture://${profileId}/save/career-1/game.sii`,
            displayName: "示例存档",
            lastModifiedMs: Date.now() - 86_400_000,
            profileLocation: "local",
          },
          {
            profileId,
            slotId: "autosave",
            folder: `fixture://${profileId}/save/autosave`,
            gameSii: `fixture://${profileId}/save/autosave/game.sii`,
            displayName: "自动保存",
            lastModifiedMs: Date.now(),
            profileLocation: "local",
          },
        ]
      : [],
  scanLocalization: async () => ({
    entries: [
      {
        key: "city.demo",
        value: "示例城市",
        sourcePath: "",
        packageName: "fixture",
        category: "city",
        status: "native",
        localeKeyPresent: true,
        defLocaleKeyPresent: true,
        unitName: "",
        localeKey: "city.demo",
      },
    ],
    packages: 2,
    inspected: 0,
    cached: 2,
    elapsedMs: 1,
  }),
  precheckCrash: async () => ({
    profileId: "fixture",
    scannedMods: 0,
    redCount: 0,
    yellowCount: 0,
    issues: [],
  }),
  inspectBsii: async () => ({ version: 0, definitions: 0, objects: 0 }),
  readSaveSnapshot: async () => ({
    version: 3,
    fields: [
      {
        structureName: "bank",
        fieldName: "money_account",
        typeId: 0x31,
        value: fixtureSaveMoney,
        offset: 0,
        size: 8,
      },
      {
        structureName: "economy",
        fieldName: "experience_points",
        typeId: 0x27,
        value: fixtureSaveExperience,
        offset: 0,
        size: 4,
      },
    ],
  }),
  mutateSave: async (_path, operation, value) => {
    if (operation === "set_money") fixtureSaveMoney = value;
    if (operation === "set_experience") fixtureSaveExperience = value;
    if (operation === "set_level") fixtureSaveExperience = value * (value - 1) * 500;
    return {
      success: true,
      operation,
      message: "Updated.",
      value,
    };
  },
  scan: async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 350));
    return { total: initialMods.length, added: 0, updated: 0, removed: 0, inspected: 0, elapsedMs: 350 };
  },
  cancelScan: async () => undefined,
  saveProfile: async () => undefined,
  launchGame: async () => undefined,
  listPresets: async () => [],
  savePreset: async () => undefined,
  loadPreset: async () => [],
};

const backend = createBackend(fixtureBackend);
let saveSelectionRequest = 0;

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

function normalizeModOrder(mods: ModRecord[], activeOrder?: string[]): ModRecord[] {
  const explicit = activeOrder ?? mods.filter((mod) => mod.enabled).map((mod) => mod.id);
  const rank = new Map(explicit.map((id, index) => [id, index]));
  const active = mods
    .filter((mod) => mod.enabled)
    .sort((left, right) => {
      const leftRank = rank.get(left.id);
      const rightRank = rank.get(right.id);
      if (leftRank !== undefined && rightRank !== undefined) return leftRank - rightRank;
      if (leftRank !== undefined) return -1;
      if (rightRank !== undefined) return 1;
      return 0;
    });
  const disabled = mods.filter((mod) => !mod.enabled);
  return [...active, ...disabled];
}

function reorderEnabled(mods: ModRecord[], id: string, targetEnabledIndex: number): ModRecord[] {
  const active = mods.filter((mod) => mod.enabled);
  const disabled = mods.filter((mod) => !mod.enabled);
  const from = active.findIndex((mod) => mod.id === id);
  if (from < 0 || targetEnabledIndex < 0 || targetEnabledIndex >= active.length || from === targetEnabledIndex) {
    return mods;
  }
  const [moved] = active.splice(from, 1);
  active.splice(targetEnabledIndex, 0, moved);
  return [...active, ...disabled];
}

interface ModState {
  language: Language;
  profiles: Profile[];
  selectedProfileId: string;
  mods: ModRecord[];
  saves: SaveSlot[];
  selectedSave: SaveSlot | null;
  saveSnapshot: SaveSnapshot | null;
  localization: LocalizationScan | null;
  diagnostics: CrashPrecheck | null;
  secondaryPanel: "none" | "localization" | "diagnostics" | "saves";
  view: ModView;
  selectedCategory: string;
  query: string;
  selectedModId: string | null;
  dirty: boolean;
  scanning: boolean;
  loading: boolean;
  error: string;
  scanSummary: ScanSummary | null;
  scanWasCancelled: boolean;
  presets: Record<string, PresetSnapshot[]>;
  selectedPresetName: string;
  setSecondaryPanel: (panel: "none" | "localization" | "diagnostics" | "saves") => void;
  scanLocalization: () => Promise<void>;
  runDiagnostics: () => Promise<void>;
  selectSave: (slot: SaveSlot | null) => Promise<void>;
  mutateSave: (operation: "set_money" | "set_experience" | "set_level", value: number) => Promise<void>;
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
  cancelScan: () => Promise<void>;
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
  saves: [],
  selectedSave: null,
  saveSnapshot: null,
  localization: null,
  diagnostics: null,
  secondaryPanel: "none",
  view: "all",
  selectedCategory: "all",
  query: "",
  selectedModId: initialMods[0]?.id ?? null,
  dirty: false,
  scanning: false,
  loading: false,
  error: "",
  scanSummary: null,
  scanWasCancelled: false,
  presets: {},
  selectedPresetName: "",
  setSecondaryPanel: (secondaryPanel) => set({ secondaryPanel }),
  scanLocalization: async () => {
    const { selectedProfileId, language } = get();
    if (!selectedProfileId) return;
    set({ loading: true, error: "" });
    try {
      const result = await backend.scanLocalization(selectedProfileId, language === "zh_CN" ? "zh_cn" : language === "ru_RU" ? "ru_ru" : "en_us");
      set({ localization: result, secondaryPanel: "localization" });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    } finally {
      set({ loading: false });
    }
  },
  runDiagnostics: async () => {
    const { selectedProfileId } = get();
    if (!selectedProfileId) return;
    set({ loading: true, error: "" });
    try {
      const result = await backend.precheckCrash(selectedProfileId);
      set({ diagnostics: result, secondaryPanel: "diagnostics" });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    } finally {
      set({ loading: false });
    }
  },
  selectSave: async (selectedSave) => {
    const requestId = ++saveSelectionRequest;
    set({
      selectedSave,
      saveSnapshot: null,
      error: "",
    });
    if (!selectedSave?.gameSii) return;
    try {
      const saveSnapshot = await backend.readSaveSnapshot(selectedSave.gameSii);
      if (
        requestId !== saveSelectionRequest
        || get().selectedSave?.gameSii !== selectedSave.gameSii
      ) {
        return;
      }
      set({ saveSnapshot, secondaryPanel: "saves" });
    } catch (error) {
      if (
        requestId !== saveSelectionRequest
        || get().selectedSave?.gameSii !== selectedSave.gameSii
      ) {
        return;
      }
      set({ error: error instanceof Error ? error.message : String(error), secondaryPanel: "saves" });
    }
  },
  mutateSave: async (operation, value) => {
    const selectedSave = get().selectedSave;
    if (!selectedSave?.gameSii) return;
    set({ loading: true, error: "" });
    try {
      const result = await backend.mutateSave(selectedSave.gameSii, operation, value);
      if (!result.success) {
        set({ error: result.message });
        return;
      }
      const saveSnapshot = await backend.readSaveSnapshot(selectedSave.gameSii);
      if (get().selectedSave?.gameSii !== selectedSave.gameSii) return;
      set({ saveSnapshot });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    } finally {
      set({ loading: false });
    }
  },
  initialize: async () => {
    saveSelectionRequest += 1;
    set({ loading: true, error: "" });
    try {
      const profiles = await backend.listProfiles();
      const selectedProfileId = profiles.some((profile) => profile.id === get().selectedProfileId)
        ? get().selectedProfileId
        : profiles[0]?.id ?? "";
      const mods = selectedProfileId ? await backend.listMods(selectedProfileId) : [];
      const saves = selectedProfileId ? await backend.listSaves(selectedProfileId) : [];
      const presets = selectedProfileId ? await backend.listPresets(selectedProfileId) : [];
      set({
        profiles,
        selectedProfileId,
        mods,
        saves,
        selectedSave: null,
        saveSnapshot: null,
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
    if (selectedProfileId === get().selectedProfileId) return;
    if (get().dirty && typeof window !== "undefined" && !window.confirm(getCopy(get().language).unsavedConfirm)) {
      return;
    }
    saveSelectionRequest += 1;
    set({ selectedProfileId, loading: true, error: "" });
    try {
      const mods = await backend.listMods(selectedProfileId);
      const saves = await backend.listSaves(selectedProfileId);
      const presets = await backend.listPresets(selectedProfileId);
      set({
        mods,
        saves,
        selectedSave: null,
        saveSnapshot: null,
        secondaryPanel: "none",
        localization: null,
        diagnostics: null,
        selectedModId: mods[0]?.id ?? null,
        presets: Object.fromEntries(presets.map((preset) => [preset.name, snapshotFromPreset(preset, mods)])),
        selectedPresetName: presets[0]?.name ?? "",
        dirty: false,
        scanWasCancelled: false,
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
    set((state) => {
      const target = state.mods.find((mod) => mod.id === id);
      if (!target) return state;
      const nextEnabled = !target.enabled;
      const activeOrder = state.mods.filter((mod) => mod.enabled && mod.id !== id).map((mod) => mod.id);
      if (nextEnabled) activeOrder.push(id);
      const mods = normalizeModOrder(
        state.mods.map((mod) => (mod.id === id ? { ...mod, enabled: nextEnabled } : mod)),
        activeOrder,
      );
      return { mods, selectedModId: id, dirty: true };
    }),
  setAll: (enabled) =>
    set((state) => ({
      mods: normalizeModOrder(state.mods.map((mod) => ({ ...mod, enabled }))),
      dirty: true,
    })),
  invertAll: () =>
    set((state) => ({
      mods: normalizeModOrder(state.mods.map((mod) => ({ ...mod, enabled: !mod.enabled }))),
      dirty: true,
    })),
  selectMod: (selectedModId) => set({ selectedModId }),
  moveMod: (id, targetIndex) =>
    set((state) => {
      const target = state.mods.find((mod) => mod.id === id);
      if (!target?.enabled) return state;
      const mods = reorderEnabled(state.mods, id, targetIndex);
      if (mods === state.mods) return state;
      return { mods, selectedModId: id, dirty: true };
    }),
  scan: async () => {
    if (get().scanning) return;
    set({ scanning: true, error: "", scanWasCancelled: false });
    try {
      const summary = await backend.scan();
      const profileId = get().selectedProfileId;
      if (profileId && !get().dirty) {
        const mods = await backend.listMods(profileId);
        set({ mods, selectedModId: mods[0]?.id ?? null, dirty: false, scanSummary: summary });
      } else {
        set({ scanSummary: summary });
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const cancelled = message.toLocaleLowerCase().includes("cancel");
      set({ error: cancelled ? "" : message, scanWasCancelled: cancelled });
    } finally {
      set({ scanning: false });
    }
  },
  cancelScan: async () => {
    if (!get().scanning) return;
    try {
      await backend.cancelScan();
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
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
      const nextMods = normalizeModOrder(
        mods.map((mod) => ({ ...mod, enabled: activeIds.has(mod.id) })),
        orderedIds,
      );
      set({ mods: nextMods, dirty: true, selectedModId: nextMods[0]?.id ?? null });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    }
  },
}));
