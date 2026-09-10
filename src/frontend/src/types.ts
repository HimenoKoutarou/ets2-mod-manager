export type Language = "zh_CN" | "en_US" | "ru_RU";
export type ModView = "all" | "active";

export interface ModRecord {
  id: string;
  packageName: string;
  displayName: string;
  author: string;
  version: string;
  source: "local" | "workshop";
  category: string;
  enabled: boolean;
  description: string;
  compatible: string;
}

export interface Profile {
  id: string;
  name: string;
  company: string;
  location: "local" | "steam" | "cloud";
  modCount: number;
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
  enabled: string;
  category: string;
  author: string;
  version: string;
  compatible: string;
  statusReady: string;
  statusDirty: string;
  statusCount: (active: number, total: number) => string;
  secondary: string;
  localization: string;
  diagnostics: string;
  saves: string;
  tools: string;
}
