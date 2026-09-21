import { create } from "zustand";
import { createBackend, type CategoryMutation, type CategorySnapshot, type CrashPrecheck, type LocalizationEntry, type LocalizationScan, type LocalModDeleteResult, type ModBackend, type ModMedia, type PresetRecord, type SaveInventory, type SaveMutation, type SaveSnapshot, type ScanSummary, type UpdateDownload, type UpdateInfo } from "./backend";
import { ALL_CATEGORIES, batchEnabled, moveBatch, type BatchAction, type MoveDirection } from "./modBatch";
import { getCopy } from "./i18n";
import { createMediaLoader, mediaKey } from "./modMedia";
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

let fixtureCategories: CategorySnapshot = {
  folders: [...new Set(initialMods.map((mod) => mod.category))],
  assignments: Object.fromEntries(initialMods.map((mod) => [mod.id, mod.category])),
};
export const fixtureBackend: ModBackend = {
  real: false,
  listCategories: async () => structuredClone(fixtureCategories),
  mutateCategories: async (request) => {
    const next = structuredClone(fixtureCategories);
    if (request.operation === "create") {
      if (next.folders.includes(request.name)) throw new Error("category_exists");
      next.folders.push(request.name);
    } else if (request.operation === "assign") {
      for (const id of request.modIds ?? []) next.assignments[id] = request.name;
    } else {
      next.folders = next.folders.flatMap((name) => name === request.name ? request.operation === "delete" ? [] : [request.newName!] : [name]);
      for (const id of Object.keys(next.assignments)) {
        if (next.assignments[id] === request.name) next.assignments[id] = request.operation === "delete" ? "" : request.newName!;
      }
    }
    fixtureCategories = next;
    return structuredClone(next);
  },
  initializeMods: async () => ({ total: initialMods.length, added: 0, updated: 0, removed: 0, inspected: 0, elapsedMs: 0 }),
  listProfiles: async () => fixtureProfiles.map((profile) => ({ ...profile })),
  listMods: async () => initialMods.map((mod) => ({ ...mod })),
  loadModMedia: async () => [],
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
        sourceName: "Demo City",
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
  readSaveInventory: async () => ({
    version: 3,
    trucks: 1,
    trailers: 1,
    garages: 1,
    cities: 2,
    dealers: 1,
    skills: 3,
    objects: [
      { objectIndex: 0, structureName: "truck", kind: "truck", fields: [
        { name: "name", typeId: 0, value: "示例卡车", offset: 0, size: 0 },
        { name: "brand", typeId: 0, value: "示例品牌", offset: 0, size: 0 },
        { name: "engine_id", typeId: 0, value: "engine.v8", offset: 0, size: 0 },
        { name: "transmission_id", typeId: 0, value: "transmission.12", offset: 0, size: 0 },
      ] },
      { objectIndex: 1, structureName: "trailer", kind: "trailer", fields: [{ name: "name", typeId: 0, value: "示例拖车", offset: 0, size: 0 }] },
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
  mutateSaveObject: async (_path, _structureName, _objectIndex, _fieldName, value) => ({
    success: true,
    operation: "set_object_field",
    message: "Updated.",
    value,
  }),
  scan: async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 350));
    return { total: initialMods.length, added: 0, updated: 0, removed: 0, inspected: 0, elapsedMs: 350 };
  },
  cancelScan: async () => undefined,
  deleteLocalMods: async (_profileId, packageNames): Promise<LocalModDeleteResult> => ({
    items: packageNames.map((packageName) => ({
      modId: packageName,
      packageName,
      displayName: packageName,
      path: `fixture://${packageName}`,
      status: "deleted",
      message: "Deleted.",
    })),
    deleted: packageNames.length,
    skipped: 0,
    failed: 0,
  }),
  cancelLocalization: async () => undefined,
  getLocalizationBase: async () => null,
  pickLocalizationBase: async () => null,
  saveLocalizationAs: async () => null,
  writeLocalizationBase: async () => undefined,
  saveProfile: async () => undefined,
  launchGame: async () => undefined,
  openModLocation: async () => undefined,
  openProfileLocation: async () => undefined,
  listPresets: async () => [],
  savePreset: async () => undefined,
  loadPreset: async () => [],
  checkUpdate: async () => ({ hasUpdate: false, latestVersion: "", currentVersion: "", releaseName: "", releaseNotes: "", assetName: "", assetSize: 0, downloadUrl: "" }),
  downloadUpdate: async () => ({ path: "" }),
  installUpdate: async () => undefined,
};

const backend = createBackend(fixtureBackend);
const initialUiProfiles = backend.real ? [] : fixtureProfiles;
const initialUiMods = backend.real ? [] : initialMods;
let saveSelectionRequest = 0;
let localizationRequest = 0;
const loadMedia = createMediaLoader(backend);

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
  saveInventory: SaveInventory | null;
  saveMutation: SaveMutation | null;
  localization: LocalizationScan | null;
  localizationBase: string | null;
  diagnostics: CrashPrecheck | null;
  secondaryPanel: "none" | "localization" | "diagnostics" | "saves";
  view: ModView;
  selectedCategory: string;
  query: string;
  selectedModId: string | null;
  selectedModIds: string[];
  categoryState: CategorySnapshot;
  categoryBusy: boolean;
  mutateCategory: (request: CategoryMutation) => Promise<boolean>;
  selectMods: (ids: string[], focusId?: string) => void;
  batchMods: (ids: string[], action: BatchAction) => void;
  moveMods: (ids: string[], direction: MoveDirection, steps?: number, beforeId?: string) => void;
  dirty: boolean;
  scanning: boolean;
  localizationScanning: boolean;
  loading: boolean;
  error: string;
  scanSummary: ScanSummary | null;
  scanWasCancelled: boolean;
  presets: Record<string, PresetSnapshot[]>;
  selectedPresetName: string;
  setSecondaryPanel: (panel: "none" | "localization" | "diagnostics" | "saves") => void;
  scanLocalization: () => Promise<void>;
  loadLocalizationBase: () => Promise<void>;
  pickLocalizationBase: () => Promise<void>;
  saveLocalizationAs: (entries: LocalizationEntry[], suggestedName: string) => Promise<void>;
  writeLocalizationBase: (entries: LocalizationEntry[]) => Promise<void>;
  cancelLocalization: () => Promise<void>;
  runDiagnostics: () => Promise<void>;
  selectSave: (slot: SaveSlot | null) => Promise<void>;
  mutateSave: (operation: "set_money" | "set_experience" | "set_level", value: number) => Promise<void>;
  mutateSaveObject: (objectIndex: number, structureName: string, fieldName: string, value: number) => Promise<void>;
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
  loadSelectedModMedia: () => Promise<void>;
  loadModMedia: (id: string) => Promise<void>;
  openModLocation: (id: string) => Promise<void>;
  deleteMods: (ids: string[]) => Promise<void>;
  openProfileLocation: (id: string) => Promise<void>;
  moveMod: (id: string, targetIndex: number) => void;
  scan: () => Promise<void>;
  cancelScan: () => Promise<void>;
  save: () => Promise<void>;
  launch: () => Promise<void>;
  savePreset: (name: string) => Promise<void>;
  selectPreset: (name: string) => void;
  loadPreset: () => Promise<void>;
  updateInfo: UpdateInfo | null;
  updateChecking: boolean;
  updateDownloading: boolean;
  updateInstalling: boolean;
  updateProgress: { downloaded: number; total: number } | null;
  updateDownloadPath: string | null;
  dismissedUpdateVersion: string | null;
  checkUpdate: () => Promise<void>;
  downloadUpdate: () => Promise<void>;
  installUpdate: () => Promise<void>;
  dismissUpdate: () => void;
}

function applyCategories(mods: ModRecord[], categories: CategorySnapshot): ModRecord[] {
  const folders = new Set(categories.folders);
  return mods.map((mod) => {
    const category = categories.assignments[mod.id] ?? "";
    return { ...mod, category: folders.has(category) ? category : "" };
  });
}

function readDismissedUpdateVersion(): string | null {
  try {
    return localStorage.getItem("ets2mm-dismissed-update");
  } catch {
    return null;
  }
}

function canEditMods(state: ModState): boolean {
  return !state.loading && state.profiles.some((profile) => profile.id === state.selectedProfileId && profile.writable !== false);
}

export const useModStore = create<ModState>((set, get) => ({
  language: "zh_CN",
  profiles: initialUiProfiles,
  selectedProfileId: initialUiProfiles[0]?.id ?? "",
  mods: initialUiMods.map((mod) => ({ ...mod })),
  saves: [],
  selectedSave: null,
  saveSnapshot: null,
  saveInventory: null,
  saveMutation: null,
  localization: null,
  localizationBase: null,
  diagnostics: null,
  secondaryPanel: "none",
  view: "all",
  selectedCategory: ALL_CATEGORIES,
  query: "",
  selectedModId: initialUiMods[0]?.id ?? null,
  selectedModIds: [],
  categoryState: { folders: [], assignments: {} },
  categoryBusy: false,
  mutateCategory: async (request) => {
    if (get().categoryBusy || get().loading || get().scanning) return false;
    set({ categoryBusy: true, error: "" });
    try {
      const categoryState = await backend.mutateCategories(request);
      set((state) => ({
        categoryState,
        mods: applyCategories(state.mods, categoryState),
        selectedCategory: state.selectedCategory === request.name && request.operation === "rename"
          ? request.newName! : state.selectedCategory === request.name && request.operation === "delete"
          ? "" : state.selectedCategory,
      }));
      return true;
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
      return false;
    } finally { set({ categoryBusy: false }); }
  },
  selectMods: (ids, focusId) => set((state) => {
    const known = new Set(state.mods.map((mod) => mod.id));
    return {
      selectedModIds: [...new Set(ids)].filter((id) => known.has(id)),
      ...(focusId ? { selectedModId: focusId } : {}),
    };
  }),
  batchMods: (ids, action) => set((state) => {
    if (!canEditMods(state)) return state;
    const mods = batchEnabled(state.mods, ids, action);
    return mods === state.mods ? state : { mods, dirty: true };
  }),
  moveMods: (ids, direction, steps = 1, beforeId) => set((state) => {
    if (!canEditMods(state)) return state;
    const mods = moveBatch(state.mods, ids, direction, steps, beforeId);
    return mods === state.mods ? state : { mods, dirty: true };
  }),
  dirty: false,
  scanning: false,
  localizationScanning: false,
  loading: false,
  error: "",
  scanSummary: null,
  scanWasCancelled: false,
  presets: {},
  selectedPresetName: "",
  updateInfo: null,
  updateChecking: false,
  updateDownloading: false,
  updateInstalling: false,
  updateProgress: null,
  updateDownloadPath: null,
  dismissedUpdateVersion: readDismissedUpdateVersion(),
  setSecondaryPanel: (secondaryPanel) => set({ secondaryPanel }),
  cancelLocalization: async () => {
    if (!get().localizationScanning) return;
    try {
      await backend.cancelLocalization();
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    }
  },
  scanLocalization: async () => {
    if (get().localizationScanning) return;
    const requestId = ++localizationRequest;
    const { selectedProfileId, language } = get();
    if (!selectedProfileId) return;
    set({ loading: true, localizationScanning: true, error: "" });
    try {
      const result = await backend.scanLocalization(
        selectedProfileId,
        language === "zh_CN" ? "zh_cn" : language === "ru_RU" ? "ru_ru" : "en_us",
        get().localizationBase,
      );
      if (
        requestId !== localizationRequest
        || get().selectedProfileId !== selectedProfileId
      ) {
        return;
      }
      set({ localization: result, secondaryPanel: "localization" });
    } catch (error) {
      if (requestId !== localizationRequest) return;
      const message = error instanceof Error ? error.message : String(error);
      set({ error: message.toLocaleLowerCase().includes("cancel") ? "" : message });
    } finally {
      if (requestId === localizationRequest) {
        set({ loading: false, localizationScanning: false });
      }
    }
  },
  loadLocalizationBase: async () => {
    try {
      set({ localizationBase: await backend.getLocalizationBase() });
    } catch {
      // Optional preference; scanning remains available without a base file.
    }
  },
  pickLocalizationBase: async () => {
    try {
      const selected = await backend.pickLocalizationBase();
      if (selected !== null) set({ localizationBase: selected });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    }
  },
  writeLocalizationBase: async (entries) => {
    const base = get().localizationBase;
    if (!base) { set({ error: "请先选择汉化基底文件。" }); return; }
    try {
      await backend.writeLocalizationBase(base, entries);
      set({ error: "" });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    }
  },
  saveLocalizationAs: async (entries, suggestedName) => {
    set({ error: "" });
    try {
      const path = await backend.saveLocalizationAs(entries, suggestedName);
      if (path) set({ localizationBase: path });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
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
      saveInventory: null,
      saveMutation: null,
      error: "",
    });
    if (!selectedSave?.gameSii) return;
    try {
      const [saveSnapshot, saveInventory] = await Promise.all([
        backend.readSaveSnapshot(selectedSave.gameSii),
        backend.readSaveInventory(selectedSave.gameSii),
      ]);
      if (
        requestId !== saveSelectionRequest
        || get().selectedSave?.gameSii !== selectedSave.gameSii
      ) {
        return;
      }
      set({ saveSnapshot, saveInventory, saveMutation: null, secondaryPanel: "saves" });
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
      const [saveSnapshot, saveInventory] = await Promise.all([
        backend.readSaveSnapshot(selectedSave.gameSii),
        backend.readSaveInventory(selectedSave.gameSii),
      ]);
      if (get().selectedSave?.gameSii !== selectedSave.gameSii) return;
      set({ saveSnapshot, saveInventory, saveMutation: result });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    } finally {
      set({ loading: false });
    }
  },
  mutateSaveObject: async (objectIndex, structureName, fieldName, value) => {
    const selectedSave = get().selectedSave;
    if (!selectedSave?.gameSii) return;
    set({ loading: true, error: "" });
    try {
      const result = await backend.mutateSaveObject(selectedSave.gameSii, structureName, objectIndex, fieldName, value);
      if (!result.success) {
        set({ error: result.message });
        return;
      }
      const saveInventory = await backend.readSaveInventory(selectedSave.gameSii);
      if (get().selectedSave?.gameSii !== selectedSave.gameSii) return;
      set({ saveInventory, saveMutation: result });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    } finally {
      set({ loading: false });
    }
  },
  initialize: async () => {
    saveSelectionRequest += 1;
    localizationRequest += 1;
    if (get().localizationScanning) {
      void backend.cancelLocalization();
    }
    set({ loading: true, localizationScanning: false, error: "" });
    try {
      const profiles = await backend.listProfiles();
      const selectedProfileId = profiles.some((profile) => profile.id === get().selectedProfileId)
        ? get().selectedProfileId
        : profiles[0]?.id ?? "";
      // Publish the real local profile catalog before any potentially expensive
      // work. A scan failure must not hide the profile picker.
      set({ profiles, selectedProfileId });
      // Load the persisted index first so the UI remains usable while the
      // incremental scanner checks additions/removals in the background.
      // Keep SQLite reads serialized during startup. WAL makes concurrent
      // readers safe, but each command also performs schema/import setup and
      // can otherwise contend with the media/cache workers.
      const categoryState = await backend.listCategories();
      const rawMods = selectedProfileId ? await backend.listMods(selectedProfileId) : [];
      const mods = applyCategories(rawMods, categoryState);
      set({
        selectedProfileId,
        mods,
        categoryState,
        selectedModIds: [],
        saves: [],
        selectedSave: null,
        saveSnapshot: null,
        saveInventory: null,
        saveMutation: null,
        selectedModId: mods[0]?.id ?? null,
        presets: {},
        selectedPresetName: "",
        dirty: false,
        scanSummary: null,
      });
      set({ loading: false });
      if (selectedProfileId) {
        void (async () => {
          try {
            const saves = await backend.listSaves(selectedProfileId);
            const presets = await backend.listPresets(selectedProfileId);
            if (get().selectedProfileId !== selectedProfileId) return;
            set({
              saves,
              presets: Object.fromEntries(presets.map((preset) => [preset.name, snapshotFromPreset(preset, get().mods)])),
              selectedPresetName: presets[0]?.name ?? "",
            });
          } catch {
            // Optional startup data must not block the Mod workspace.
          }
        })();
      }
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
    localizationRequest += 1;
    if (get().localizationScanning) {
      void backend.cancelLocalization();
    }
    set({ selectedProfileId, loading: true, localizationScanning: false, error: "" });
    try {
      const categoryState = await backend.listCategories();
      const rawMods = await backend.listMods(selectedProfileId);
      const mods = applyCategories(rawMods, categoryState);
      set({
        mods,
        categoryState,
        selectedModIds: [],
        saves: [],
        selectedSave: null,
        saveSnapshot: null,
        saveInventory: null,
        saveMutation: null,
        secondaryPanel: "none",
        localization: null,
        diagnostics: null,
        selectedModId: mods[0]?.id ?? null,
        presets: {},
        selectedPresetName: "",
        dirty: false,
        scanWasCancelled: false,
      });
      void (async () => {
        try {
          const saves = await backend.listSaves(selectedProfileId);
          const presets = await backend.listPresets(selectedProfileId);
          if (get().selectedProfileId !== selectedProfileId) return;
          set({
            saves,
            presets: Object.fromEntries(presets.map((preset) => [preset.name, snapshotFromPreset(preset, get().mods)])),
            selectedPresetName: presets[0]?.name ?? "",
          });
        } catch {
          // Optional profile data must not block profile switching.
        }
      })();
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    } finally {
      set({ loading: false });
    }
  },
  setView: (view) => set({ view, selectedModIds: [] }),
  setCategory: (selectedCategory) => set({ selectedCategory, selectedModIds: [] }),
  setQuery: (query) => set({ query, selectedModIds: [] }),
  toggleMod: (id) =>
    set((state) => {
      if (!canEditMods(state)) return state;
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
  selectMod: (selectedModId) => set({ selectedModId, selectedModIds: [selectedModId] }),
  loadSelectedModMedia: async () => {
    const id = get().selectedModId;
    if (id) await get().loadModMedia(id);
  },
  loadModMedia: async (id) => {
    const { selectedProfileId, mods } = get();
    const selected = mods.find((mod) => mod.id === id);
    if (!selected || selected.mediaLoaded) return;
    const key = mediaKey(selected);
    let entry: ModMedia | undefined;
    let loaded = false;
    try {
      entry = await loadMedia(selected);
      loaded = true;
    } catch {
      // Transient extractor / SQLite contention must not permanently suppress
      // the thumbnail. ModThumbnail retries a bounded number of times.
    }
    if (get().selectedProfileId !== selectedProfileId) return;
    set((state) => ({
      mods: state.mods.map((mod) =>
        mediaKey(mod) === key
          ? {
              ...mod,
              iconUrl: entry?.iconUrl ?? mod.iconUrl,
              previewUrl: entry?.previewUrl ?? mod.previewUrl,
              mediaLoaded: loaded,
              mediaAttempts: loaded ? mod.mediaAttempts : (mod.mediaAttempts ?? 0) + 1,
            }
          : mod,
      ),
    }));
  },
  openModLocation: async (id) => {
    const mod = get().mods.find((candidate) => candidate.id === id);
    if (mod) await backend.openModLocation(mod);
  },
  deleteMods: async (ids) => {
    const { selectedProfileId, mods, categoryState } = get();
    if (!selectedProfileId || !ids.length || !canEditMods(get())) return;
    const packageNames = ids
      .map((id) => mods.find((mod) => mod.id === id))
      .filter((mod): mod is ModRecord => Boolean(mod && mod.source === "local" && !mod.enabled))
      .map((mod) => mod.packageName);
    if (!packageNames.length) return;
    set({ loading: true, error: "" });
    try {
      const result = await backend.deleteLocalMods(selectedProfileId, packageNames);
      if (get().selectedProfileId !== selectedProfileId) return;
      const refreshed = applyCategories(await backend.listMods(selectedProfileId), categoryState);
      set({
        mods: refreshed,
        selectedModIds: [],
        selectedModId: refreshed[0]?.id ?? null,
        dirty: false,
        error: getCopy(get().language).deleteLocalSummary(result.deleted, result.skipped, result.failed),
      });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    } finally {
      set({ loading: false });
    }
  },
  openProfileLocation: async (id) => {
    const profile = get().profiles.find((candidate) => candidate.id === id);
    if (profile) await backend.openProfileLocation(profile);
  },
  moveMod: (id, targetIndex) =>
    set((state) => {
      const target = state.mods.find((mod) => mod.id === id);
      if (!target?.enabled) return state;
      const mods = reorderEnabled(state.mods, id, targetIndex);
      if (mods === state.mods) return state;
      return { mods, selectedModId: id, dirty: true };
    }),
  scan: async () => {
    if (get().scanning || get().categoryBusy) return;
    set({ scanning: true, error: "", scanWasCancelled: false });
    try {
      const summary = await backend.scan();
      const profileId = get().selectedProfileId;
      if (profileId && !get().dirty) {
        const categoryState = await backend.listCategories();
        const mods = applyCategories(await backend.listMods(profileId), categoryState);
        set({ mods, categoryState, selectedModIds: [], selectedModId: mods[0]?.id ?? null, dirty: false, scanSummary: summary });
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
  checkUpdate: async () => {
    if (get().updateChecking) return;
    set({ updateChecking: true });
    try {
      const updateInfo = await backend.checkUpdate();
      set({ updateInfo });
    } catch {
      set({ updateInfo: null });
    } finally {
      set({ updateChecking: false });
    }
  },
  downloadUpdate: async () => {
    const updateInfo = get().updateInfo;
    if (!updateInfo?.downloadUrl || get().updateDownloading || get().updateInstalling) return;
    set({ updateDownloading: true, updateProgress: null, error: "" });
    try {
      const result = await backend.downloadUpdate(
        updateInfo.downloadUrl,
        updateInfo.assetName || "ets2-mod-manager-update.exe",
      );
      set({ updateDownloadPath: result.path });
      // Download finished: kick off the silent install automatically.
      await get().installUpdate();
    } catch (error) {
      set({ error: error instanceof Error ? error.message : String(error) });
    } finally {
      set({ updateDownloading: false });
    }
  },
  installUpdate: async () => {
    const path = get().updateDownloadPath;
    if (!path || get().updateInstalling) return;
    set({ updateInstalling: true, error: "" });
    try {
      await backend.installUpdate(path);
    } catch (error) {
      set({ updateInstalling: false, error: error instanceof Error ? error.message : String(error) });
    }
  },
  dismissUpdate: () => {
    const version = get().updateInfo?.latestVersion ?? "";
    if (!version) return;
    try {
      localStorage.setItem("ets2mm-dismissed-update", version);
    } catch {
      // localStorage may be unavailable in restricted webviews.
    }
    set({ dismissedUpdateVersion: version });
  },
}));
