export type Language = "zh_CN" | "en_US" | "ru_RU";
export type ModView = "all" | "active";

export interface ModRecord {
  id: string;
  packageName: string;
  path?: string;
  packageType?: string;
  displayName: string;
  author: string;
  version: string;
  source: "local" | "workshop";
  category: string;
  enabled: boolean;
  description: string;
  compatible: string;
  size?: number;
  modifiedMs?: number;
}

export interface Profile {
  id: string;
  name: string;
  company: string;
  location: "local" | "steam" | "cloud";
  modCount: number;
  folder?: string;
  writable?: boolean;
}

export interface SaveSlot {
  profileId: string;
  slotId: string;
  folder: string;
  gameSii: string;
  displayName: string;
  lastModifiedMs: number;
  profileLocation: "local" | "readonly";
}

export interface UiCopy {
  appTitle: string;
  modWorkspace: string;
  scan: string;
  save: string;
  launch: string;
  profiles: string;
  categories: string;
  allMods: string;
  activeMods: string;
  searchPlaceholder: string;
  enableAll: string;
  disableAll: string;
  invert: string;
  moveTop: string;
  moveUp: string;
  moveDown: string;
  moveBottom: string;
  presetPlaceholder: string;
  savePreset: string;
  loadPreset: string;
  noSelection: string;
  selectHint: string;
  sourceLocal: string;
  sourceWorkshop: string;
  source: string;
  name: string;
  package: string;
  priority: string;
  enabled: string;
  category: string;
  author: string;
  version: string;
  compatible: string;
  readOnly: string;
  localProfile: string;
  launchUnavailable: string;
  noMods: string;
  statusReady: string;
  statusDirty: string;
  statusCount: (active: number, total: number) => string;
  secondary: string;
  localization: string;
  diagnostics: string;
  saves: string;
  tools: string;
  saveCount: (count: number) => string;
  noSaves: string;
  saveUpdated: string;
  autosave: string;
}
