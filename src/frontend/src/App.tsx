import { useEffect, useMemo, useRef, useState } from "react";
import { getCurrentWindow, Window } from "@tauri-apps/api/window";
import type { UnlistenFn } from "@tauri-apps/api/event";
import { listen } from "@tauri-apps/api/event";
import {
  ArrowDown,
  ArrowDownToLine,
  ArrowLeft,
  ArrowUp,
  ArrowUpToLine,
  Check,
  CheckSquare,
  ChevronDown,
  FolderOpen,
  Languages,
  LayoutGrid,
  Play,
  RefreshCw,
  Save,
  Search,
  FolderInput,
  ExternalLink,
  Sparkles,
  Wrench,
  X,
} from "lucide-react";
import { getCopy } from "./i18n";
import { isTauriRuntime } from "./backend";
import InitializerWindow from "./InitializerWindow";
import { useModStore } from "./store";
import { ModImage, ModThumbnail } from "./ModImage";
import ModDirectoryDialog, { directoryCopy } from "./ModDirectoryDialog";
import CategorySidebar, { TriCheckbox, readDraggedMods } from "./CategorySidebar";
import { ALL_CATEGORIES } from "./modBatch";
import { categoryCopy, categoryError, localizationCategory } from "./categoryI18n";
import type { SearchMode } from "./types";

type SaveDraftKey = "money_account" | "experience_points" | "level";

function isValidSaveDraft(value: string, minimum: number, maximum: number): boolean {
  if (!value.trim()) return false;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= minimum && parsed <= maximum;
}

function levelFromExperience(value: number): number {
  const experience = Math.max(0, Math.floor(value));
  let level = 1;
  while (level < 200 && experience >= level * (level + 1) * 500) {
    level += 1;
  }
  return level;
}

function App() {
  const windowLabel = isTauriRuntime() ? getCurrentWindow().label : "main";
  const {
    language,
    profiles,
    selectedProfileId,
    mods,
    saves,
    selectedSave,
    saveSnapshot,
    saveInventory,
    saveMutation,
    localization,
    localizationBase,
    diagnostics,
    secondaryPanel,
    view,
    selectedCategory,
    query,
    selectedModId,
    selectedModIds,
    categoryState,
    categoryBusy,
    mutateCategory,
    selectMods,
    batchMods,
    moveMods,
    dirty,
    scanning,
    localizationScanning,
    scanSummary,
    scanWasCancelled,
    loading,
    presets,
    selectedPresetName,
    setLanguage,
    selectProfile,
    setView,
    setCategory,
    setQuery,
    toggleMod,
    loadSelectedModMedia,
    loadModMedia,
    openModLocation,
    openProfileLocation,
    scan,
    cancelScan,
    save,
    launch,
    savePreset,
    selectPreset,
    loadPreset,
    setSecondaryPanel,
    selectSave,
    mutateSave,
    scanLocalization,
    loadLocalizationBase,
    pickLocalizationBase,
    writeLocalizationBase,
    saveLocalizationAs,
    cancelLocalization,
    runDiagnostics,
    initialize,
    error,
    updateInfo,
    updateDownloading,
    updateInstalling,
    updateProgress,
    updateDownloadPath,
    dismissedUpdateVersion,
    checkUpdate,
    downloadUpdate,
    dismissUpdate,
  } = useModStore();
  const [presetName, setPresetName] = useState("");
  const [activePage, setActivePage] = useState<"mods" | "profiles" | "save" | "localization">("mods");
  const [directoryOpen, setDirectoryOpen] = useState(false);
  const [batchScope, setBatchScope] = useState<"selection" | "filtered" | "category">("filtered");
  const [searchMode, setSearchMode] = useState<SearchMode>("fuzzy");
  const [contextMenu, setContextMenu] = useState<{ modId: string; x: number; y: number } | null>(null);
  const [profileContextMenu, setProfileContextMenu] = useState<{ profileId: string; x: number; y: number } | null>(null);
  const [contextMoveOpen, setContextMoveOpen] = useState(false);
  const [contextCategoryOpen, setContextCategoryOpen] = useState(false);
  const [moveSteps, setMoveSteps] = useState(1);
  const selectionAnchor = useRef<string | null>(null);
  const tableWrapRef = useRef<HTMLDivElement>(null);
  const startupLoad = useRef<Promise<void> | null>(null);
  const [saveValues, setSaveValues] = useState<Record<SaveDraftKey, string>>({
    money_account: "",
    experience_points: "",
    level: "",
  });
  const [localizationDraft, setLocalizationDraft] = useState<Record<string, string>>({});
  const [localizationKeyDraft, setLocalizationKeyDraft] = useState<Record<string, string>>({});
  const [localizationProgress, setLocalizationProgress] = useState<{ packageName: string; path?: string; processed: number; total: number } | null>(null);
  const [localizationFileProgress, setLocalizationFileProgress] = useState<{ packageName: string; file: string } | null>(null);
  const [updateDownloadProgress, setUpdateDownloadProgress] = useState<{ downloaded: number; total: number } | null>(null);
  useEffect(() => {
    if (!isTauriRuntime()) return;
    let stop: UnlistenFn | undefined;
    let stopFile: UnlistenFn | undefined;
    let stopDownload: UnlistenFn | undefined;
    void listen<{ packageName: string; path?: string; processed: number; total: number }>("localization-progress", (event) => {
      setLocalizationProgress(event.payload);
      setLocalizationFileProgress({
        packageName: event.payload.packageName || event.payload.path?.split(/[\\/]/).pop() || "",
        file: "",
      });
    }).then((unlisten) => { stop = unlisten; });
    void listen<{ packageName: string; file: string }>("localization-file-progress", (event) => {
      setLocalizationFileProgress(event.payload);
    }).then((unlisten) => { stopFile = unlisten; });
    void listen<{ downloaded: number; total: number }>("update-download-progress", (event) => {
      setUpdateDownloadProgress(event.payload);
    }).then((unlisten) => { stopDownload = unlisten; });
    return () => { stop?.(); stopFile?.(); stopDownload?.(); };
  }, []);
  useEffect(() => {
    if (!contextMenu) return;
    const close = () => {
      setContextMenu(null);
      setContextMoveOpen(false);
      setContextCategoryOpen(false);
    };
    window.addEventListener("click", close);
    return () => {
      window.removeEventListener("click", close);
    };
  }, [contextMenu]);
  useEffect(() => {
    if (!profileContextMenu) return;
    const close = () => setProfileContextMenu(null);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [profileContextMenu]);
  useEffect(() => {
    if (windowLabel === "initializer") return;
    if (!isTauriRuntime()) {
      void initialize();
      return;
    }
    let unlisten: UnlistenFn | undefined;
    let readyTimer: number | undefined;
    let active = true;
    void (async () => {
      const current = getCurrentWindow();
      unlisten = await current.listen("ets2-initialization-complete", () => {
        if (!active || startupLoad.current) return;
        startupLoad.current = (async () => {
          try {
            await initialize();
            const failure = useModStore.getState().error;
            if (failure) throw new Error(failure);
            await current.show();
            const initializer = await Window.getByLabel("initializer");
            await initializer?.close();
            window.clearInterval(readyTimer);
          } catch (reason) {
            startupLoad.current = null;
            await current.emitTo("initializer", "ets2-initialization-error", String(reason));
          }
        })();
      });
      if (!active) {
        unlisten();
        return;
      }
      // Repeat the handshake in case the other webview subscribes later.
      const ready = () => { void current.emitTo("initializer", "ets2-main-ready").catch(() => {}); };
      ready();
      readyTimer = window.setInterval(ready, 500);
    })().catch((reason) => useModStore.setState({ error: String(reason) }));
    return () => {
      active = false;
      window.clearInterval(readyTimer);
      unlisten?.();
    };
  }, [initialize, windowLabel]);
  useEffect(() => {
    setSaveValues({
      money_account: "",
      experience_points: "",
      level: "",
    });
  }, [selectedSave?.gameSii]);
  useEffect(() => { void loadLocalizationBase(); }, [loadLocalizationBase]);
  useEffect(() => { void checkUpdate(); }, [checkUpdate]);
  const text = getCopy(language);
  const saveToolLabels = language === "zh_CN"
    ? { profile: "Profile", trucks: "卡车", trailers: "拖车", detected: "已识别", object: "对象", truckObject: "卡车对象", trailerObject: "拖车对象" }
    : language === "ru_RU"
      ? { profile: "Профиль", trucks: "Грузовики", trailers: "Прицепы", detected: "найдено", object: "объектов", truckObject: "объект грузовика", trailerObject: "объект прицепа" }
      : { profile: "Profile", trucks: "Trucks", trailers: "Trailers", detected: "detected", object: "objects", truckObject: "Truck object", trailerObject: "Trailer object" };
  const categoryText = categoryCopy[language];
  const selectedProfile = profiles.find((profile) => profile.id === selectedProfileId) ?? profiles[0] ?? {
    id: "",
    name: text.profiles,
    company: "",
    location: "local" as const,
    modCount: 0,
  };
  const filteredMods = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return mods.filter((mod) => {
      const matchesView = view === "all" || mod.enabled;
      const matchesCategory = selectedCategory === ALL_CATEGORIES || mod.category === selectedCategory;
      if (!matchesView) return false;
      if (!matchesCategory) return false;
      if (!needle) return true;
      const values = [mod.displayName, mod.author, mod.packageName, mod.category, mod.id]
        .map((value) => value.trim().toLocaleLowerCase());
      return searchMode === "exact"
        ? values.some((value) => value === needle)
        : values.some((value) => value.includes(needle));
    });
  }, [mods, query, searchMode, selectedCategory, view]);
  const selectedMod = mods.find((mod) => mod.id === selectedModId) ?? null;
  const contextMod = contextMenu ? mods.find((mod) => mod.id === contextMenu.modId) ?? null : null;
  const contextProfile = profileContextMenu ? profiles.find((profile) => profile.id === profileContextMenu.profileId) ?? null : null;
  async function openProfilePanel(profileId: string, panel: "saves" | "localization" | "diagnostics") {
    await selectProfile(profileId);
    setActivePage(panel === "localization" ? "localization" : "mods");
    setSecondaryPanel(panel);
    if (panel === "diagnostics") await runDiagnostics();
  }
  useEffect(() => {
    if (selectedMod) void loadSelectedModMedia();
  }, [selectedMod?.id, selectedMod?.iconUrl, selectedMod?.previewUrl, loadSelectedModMedia]);
  useEffect(() => {
    const root = tableWrapRef.current;
    if (!root || !mods.length) return;
    let scheduled = false;
    const requestVisibleMedia = () => {
      scheduled = false;
      const rootRect = root.getBoundingClientRect();
      const minTop = rootRect.top - 320;
      const maxBottom = rootRect.bottom + 320;
      root.querySelectorAll<HTMLElement>("tr[data-mod-id]").forEach((row) => {
        const rect = row.getBoundingClientRect();
        if (rect.bottom < minTop || rect.top > maxBottom) return;
        const id = row.dataset.modId;
        const mod = id ? mods.find((candidate) => candidate.id === id) : undefined;
        if (mod && !mod.mediaLoaded && (mod.mediaAttempts ?? 0) < 3) {
          void loadModMedia(mod.id);
        }
      });
    };
    const onScroll = () => {
      if (scheduled) return;
      scheduled = true;
      window.requestAnimationFrame(requestVisibleMedia);
    };
    requestVisibleMedia();
    root.addEventListener("scroll", onScroll, { passive: true });
    return () => root.removeEventListener("scroll", onScroll);
  }, [filteredMods, mods, loadModMedia]);
  const activeCount = mods.filter((mod) => mod.enabled).length;
  const selectedIds = new Set(selectedModIds);
  const visibleIds = filteredMods.map((mod) => mod.id);
  const categoryIds = selectedCategory === ALL_CATEGORIES ? [] : mods.filter((mod) => mod.category === selectedCategory).map((mod) => mod.id);
  const batchIds = batchScope === "selection" ? selectedModIds : batchScope === "category" ? categoryIds : visibleIds;
  const batchIdSet = new Set(batchIds);
  const canBatch = selectedProfile.writable !== false && !!selectedProfileId && !loading && batchIds.length > 0;
  const canMove = canBatch && mods.some((mod) => mod.enabled && batchIdSet.has(mod.id));
  const formatSaveDate = (value: number) =>
    value > 0
      ? new Intl.DateTimeFormat(language.replace("_", "-"), {
          dateStyle: "medium",
          timeStyle: "short",
        }).format(new Date(value))
      : "—";

  function moveSelected(target: "top" | "up" | "down" | "bottom") {
    moveMods(batchIds, target, moveSteps);
  }

  function onDrop(id: string, event: React.DragEvent<HTMLTableRowElement>) {
    event.preventDefault();
    moveMods(readDraggedMods(event), "top", 1, id);
  }

  function selectRow(id: string, event: React.MouseEvent) {
    if (event.shiftKey && selectionAnchor.current && visibleIds.includes(selectionAnchor.current)) {
      const a = visibleIds.indexOf(selectionAnchor.current);
      const b = visibleIds.indexOf(id);
      const range = visibleIds.slice(Math.min(a, b), Math.max(a, b) + 1);
      selectMods(event.ctrlKey || event.metaKey ? [...selectedModIds, ...range] : range, id);
    } else {
      selectMods(event.ctrlKey || event.metaKey ? selectedIds.has(id) ? selectedModIds.filter((value) => value !== id) : [...selectedModIds, id] : [id], id);
      selectionAnchor.current = id;
    }
    setBatchScope("selection");
  }

  function chooseCategory(category: string) {
    setCategory(category);
    setBatchScope(category === ALL_CATEGORIES ? "filtered" : "category");
    selectionAnchor.current = null;
  }

  function updateSaveDraft(key: SaveDraftKey, value: string) {
    setSaveValues((current) => ({ ...current, [key]: value }));
  }

  function submitSaveDraft(
    key: SaveDraftKey,
    operation: "set_money" | "set_experience" | "set_level",
    minimum: number,
    maximum: number,
  ) {
    const raw = saveValues[key];
    if (!isValidSaveDraft(raw, minimum, maximum)) return;
    void mutateSave(operation, Number(raw));
    updateSaveDraft(key, "");
  }

  function openSaveEditor(saveSlot: typeof selectedSave) {
    if (!saveSlot) return;
    setActivePage("save");
    void selectSave(saveSlot);
  }

  if (windowLabel === "initializer") return <InitializerWindow />;

  const updateDismissed = Boolean(
    updateInfo && dismissedUpdateVersion && updateInfo.latestVersion === dismissedUpdateVersion,
  );
  const showUpdateDialog = Boolean(
    updateInfo?.hasUpdate
    && !updateDismissed
    && (updateDownloading || updateInstalling || !updateDownloadPath),
  );
  const downloadPercent = updateDownloadProgress?.total
    ? Math.min(100, Math.floor((updateDownloadProgress.downloaded / updateDownloadProgress.total) * 100))
    : 0;

  const renderReleaseNotes = (notes: string) => {
    if (!notes.trim()) return null;
    const lines = notes.split("\n").filter((line) => {
      // Drop auto-generated link-only lines (e.g. "Full Changelog: https://...").
      const trimmed = line.trim();
      return !/^https?:\/\/\S+$/.test(trimmed);
    });
    return lines.map((line, index) => {
      const trimmed = line.trim();
      if (trimmed.startsWith("## ")) {
        return <h4 className="update-note-heading" key={index}>{trimmed.slice(3)}</h4>;
      }
      if (trimmed.startsWith("# ")) {
        return <h4 className="update-note-heading" key={index}>{trimmed.slice(2)}</h4>;
      }
      if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
        return <div className="update-note-item" key={index}>• {trimmed.slice(2)}</div>;
      }
      if (trimmed === "") {
        return <div className="update-note-gap" key={index} />;
      }
      return <p className="update-note-line" key={index}>{trimmed}</p>;
    });
  };

  const saveLocalizationAsClick = () => {
    if (!localization) return;
    const entries = localization.entries.map((entry) => {
      const manualKey = localizationKeyDraft[entry.key]?.trim();
      const key = manualKey || entry.key;
      return { ...entry, key, localeKey: key, value: localizationDraft[entry.key] ?? entry.value };
    });
    const baseName = (localizationBase?.split(/[\\/]/).pop() ?? "localization").replace(/\.(sii|sui)$/i, "");
    void saveLocalizationAs(entries, `${baseName}_zh.sui`);
  };

  return (
    <div className={`app-shell ${activePage === "localization" ? "localization-page" : ""}`}>
      {updateInfo?.hasUpdate && !updateDismissed ? (
        <div className="update-banner">
          <Sparkles size={15} />
          <span>{text.updateAvailable} v{updateInfo.latestVersion}（当前 v{updateInfo.currentVersion}）</span>
          {updateDownloadPath ? (
            <span className="update-downloaded" title={updateDownloadPath}>{text.updateDownloaded}</span>
          ) : (
            <button className="button" onClick={() => { void downloadUpdate(); }} disabled={updateDownloading}>
              {updateDownloading ? text.updateDownloading : text.updateDownload}
            </button>
          )}
        </div>
      ) : null}
      {showUpdateDialog && updateInfo ? (
        <div className="update-dialog-overlay">
          <div className="update-dialog">
            <div className="update-dialog-header">
              <div className="update-dialog-icon"><Sparkles size={20} /></div>
              <div className="update-dialog-title">
                <strong>{text.updateAvailable}</strong>
                <span>v{updateInfo.latestVersion} · 当前 v{updateInfo.currentVersion}</span>
              </div>
            </div>
            {updateInstalling ? (
              <div className="update-dialog-installing">
                <RefreshCw size={18} className="spin" />
                <span>{text.updateInstalling}</span>
              </div>
            ) : updateDownloading ? (
              <div className="update-dialog-progress">
                <div className="update-dialog-progress-track">
                  <span style={{ width: `${downloadPercent}%` }} />
                </div>
                <span className="update-dialog-progress-copy">{text.updateProgress(downloadPercent)}</span>
              </div>
            ) : (
              <>
                <div className="update-dialog-notes">
                  {updateInfo.releaseNotes ? renderReleaseNotes(updateInfo.releaseNotes) : (
                    <p className="update-note-line">{updateInfo.releaseName}</p>
                  )}
                </div>
                <div className="update-dialog-actions">
                  <button className="button button-primary" onClick={() => { void downloadUpdate(); }}>
                    {text.updateInstall}
                  </button>
                  <button className="button" onClick={dismissUpdate}>{text.updateIgnore}</button>
                </div>
              </>
            )}
          </div>
        </div>
      ) : null}
      <header className="app-header">
        <div className="brand-block">
          <div className="brand-mark"><Sparkles size={17} /></div>
          <div>
            <div className="brand-title">{text.appTitle}</div>
            <div className="brand-context">{selectedProfile.name} · {text.modWorkspace}</div>
          </div>
        </div>
        <nav className="page-nav" aria-label="Primary navigation">
          <button className={activePage === "mods" ? "is-active" : ""} onClick={() => setActivePage("mods")}>
            <LayoutGrid size={15} />{text.modPage}
          </button>
          <button className={activePage === "profiles" ? "is-active" : ""} onClick={() => setActivePage("profiles")}>
            <FolderOpen size={15} />{text.profilePage}
          </button>
          <button className={activePage === "localization" ? "is-active" : ""} onClick={() => { setActivePage("localization"); setSecondaryPanel("localization"); }}>
            <Sparkles size={15} />{text.localization}
          </button>
        </nav>
        <div className="header-actions">
          <button className="button button-quiet" disabled={loading || scanning || localizationScanning || !isTauriRuntime()} onClick={() => setDirectoryOpen(true)}>
            <FolderOpen size={16} />{directoryCopy[language].title}
          </button>
          <button className={`button ${scanning ? "button-warning" : "button-primary"}`} disabled={categoryBusy} onClick={() => { void (scanning ? cancelScan() : scan()); }}>
            <RefreshCw size={16} className={scanning ? "spin" : ""} />{scanning ? text.cancelScan : text.scan}
          </button>
          <button className="button button-primary" onClick={() => { void save(); }} disabled={!dirty || selectedProfile.writable === false}><Save size={16} />{text.save}</button>
          <button className="button button-quiet" onClick={() => { void launch(); }}><Play size={16} />{text.launch}</button>
          <label className="language-picker" title="Language">
            <Languages size={16} />
            <select value={language} onChange={(event) => setLanguage(event.target.value as typeof language)}>
              <option value="zh_CN">简体中文</option>
              <option value="en_US">English</option>
              <option value="ru_RU">Русский</option>
            </select>
            <ChevronDown size={14} />
          </label>
        </div>
      </header>

      {activePage === "localization" ? (
        <main className="localization-page-main">
          <section className="localization-page-inner">
            <div className="page-heading localization-heading">
              <div>
                <div className="panel-title">{text.localization}</div>
                <div className="panel-subtitle">{selectedProfile.name} · {text.localizationBase}</div>
              </div>
              <button className="button" onClick={() => setActivePage("mods")}><ArrowLeft size={16} />{text.modPage}</button>
            </div>

            <div className="localization-toolbar">
              <label className="localization-select">
                <span>{text.profiles}</span>
                <select
                  value={selectedProfileId}
                  onChange={(event) => {
                    if (event.target.value) void selectProfile(event.target.value);
                  }}
                >
                  {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
                </select>
              </label>
              <label className="localization-select">
                <span>{categoryText.category}</span>
                <select value={selectedCategory} onChange={(event) => chooseCategory(event.target.value)}>
                  <option value={ALL_CATEGORIES}>{text.allMods}</option>
                  {categoryState.folders.filter((name) => name !== categoryText.uncategorized).map((name) => <option key={name} value={name}>{name}</option>)}
                </select>
              </label>
              <div className="localization-toolbar-spacer" />
              <button className="button" onClick={() => { void pickLocalizationBase(); }}>
                <FolderOpen size={14} />{text.chooseLocalizationBase}
              </button>
            </div>

            <section className="localization-control-panel">
              <div className="localization-base-summary">
                <span className="localization-base-label">{text.localizationBase}</span>
                <strong title={localizationBase ?? ""}>{localizationBase ? localizationBase.split(/[\\/]/).pop() : text.noneFound}</strong>
              </div>
              <button
                className="button button-primary"
                onClick={() => { void (localizationScanning ? cancelLocalization() : scanLocalization()); }}
                disabled={scanning || (!localizationScanning && !localizationBase)}
              >
                <Sparkles size={14} />{localizationScanning ? text.cancelScan : text.scanLocalization}
              </button>
              <button
                className="button"
                disabled={!localization?.entries.length || localizationScanning}
                onClick={saveLocalizationAsClick}
              >
                <Save size={14} />{text.saveLocalizationAs}
              </button>
            </section>

            <section className={`localization-progress-card ${localizationScanning ? "is-active" : ""}`}>
              <div className="localization-progress-topline">
                <strong>{localizationScanning ? text.scanning : text.localization}</strong>
                <span>{localizationProgress?.processed ?? 0} / {localizationProgress?.total ?? 0}</span>
              </div>
              <div className="localization-progress-track"><span /></div>
              <div className="localization-progress-details">
                <span title={localizationFileProgress?.packageName ?? localizationProgress?.packageName ?? ""}>
                  {localizationFileProgress?.packageName || localizationProgress?.packageName || text.noSelection}
                </span>
                <code title={localizationFileProgress?.file ?? ""}>{localizationFileProgress?.file || text.localizationFile}</code>
              </div>
            </section>

            <section className="localization-results-panel">
              <div className="section-heading">
                <span>{text.localization}</span>
                <span className="section-count">{localization ? text.entriesSummary(localization.entries.length, localization.cached, localization.inspected) : text.noSelection}</span>
              </div>
              {localization?.entries.length ? (
                <div className="localization-results">
                  {localization.entries.map((entry) => (
                    <div className="localization-result-row" key={`${entry.packageName}:${entry.key}`}>
                      <span className="localization-result-kind">{localizationCategory(entry.category, language)}</span>
                      <div className="localization-result-key">
                        <strong title={entry.sourceName || entry.key}>{entry.sourceName || entry.key}</strong>
                        {entry.key.startsWith("__manual_") ? (
                          <input
                            className="localization-key-input"
                            value={localizationKeyDraft[entry.key] ?? ""}
                            placeholder={text.fillLocalizationKey}
                            onChange={(event) => setLocalizationKeyDraft((current) => ({ ...current, [entry.key]: event.target.value }))}
                          />
                        ) : (
                          <code title={entry.key}>@@{entry.key}@@</code>
                        )}
                        <small>{entry.packageName}</small>
                      </div>
                      <code title={entry.sourcePath}>{entry.sourcePath.split("::").pop() ?? entry.sourcePath}</code>
                      <input
                        value={localizationDraft[entry.key] ?? entry.value}
                        placeholder={entry.value || "—"}
                        onChange={(event) => setLocalizationDraft((current) => ({ ...current, [entry.key]: event.target.value }))}
                      />
                    </div>
                  ))}
                </div>
              ) : (
                <div className="detail-empty localization-empty"><Sparkles size={28} /><strong>{text.noSelection}</strong><span>{text.scanLocalization}</span></div>
              )}
            </section>
          </section>
        </main>
      ) : activePage === "save" ? (
        <main className="save-editor-page">
          <section className="save-editor-main">
            <div className="page-heading">
              <div>
                <div className="panel-title">{text.saves}</div>
                <div className="panel-subtitle">{selectedProfile.name} · {text.saveCount(saves.length)}</div>
              </div>
              <button className="button" onClick={() => setActivePage("profiles")}><ArrowLeft size={16} />{text.profilePage}</button>
            </div>
            <div className="save-editor-layout">
              <section className="save-browser-panel">
                <div className="section-heading"><span>{text.saves}</span><span className="section-count">{saves.length}</span></div>
                {saves.length === 0 ? (
                  <div className="detail-empty compact"><FolderOpen size={24} /><span>{text.noSaves}</span></div>
                ) : (
                  <div className="save-list">
                    {saves.map((saveSlot) => (
                      <button
                        className={`save-item ${selectedSave?.slotId === saveSlot.slotId ? "is-selected" : ""} ${saveSlot.slotId.toLowerCase().startsWith("autosave") ? "is-autosave" : ""}`}
                        key={saveSlot.slotId}
                        onClick={() => openSaveEditor(saveSlot)}
                      >
                        <div className="save-item-icon"><FolderOpen size={15} /></div>
                        <div className="save-item-copy">
                          <strong>{saveSlot.displayName}</strong>
                          <small>{saveSlot.slotId.toLowerCase().startsWith("autosave") ? text.autosave : text.saveUpdated} · {formatSaveDate(saveSlot.lastModifiedMs)}</small>
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </section>
              <section className="save-workbench-panel">
                {selectedSave ? (
                  <>
                    <div className="save-workbench-heading">
                      <div>
                        <div className="panel-title">{selectedSave.displayName}</div>
                        <div className="panel-subtitle">{selectedSave.folder}</div>
                      </div>
                      <span className={`source source-${selectedSave.profileLocation}`}>{selectedSave.profileLocation === "local" ? text.sourceLocal : text.readOnly}</span>
                    </div>
                    <div className="save-tool-sections">
                      <section className="save-tool-section">
                        <div className="save-tool-section-heading">
                          <div><strong>{saveToolLabels.profile}</strong><span>{text.profileDetails}</span></div>
                          <span className="save-tool-count">{saveInventory?.objects.filter((object) => object.kind === "profile" || object.kind === "garage").length ?? 0}</span>
                        </div>
                        <div className="save-tool-card-grid">
                          <article className="save-tool-card">
                            <div className="save-tool-card-icon"><Wrench size={16} /></div>
                            <div><strong>{text.money} / {text.experience}</strong><span>{saveSnapshot ? "BSII snapshot" : text.scanning}</span></div>
                          </article>
                          <article className="save-tool-card">
                            <div className="save-tool-card-icon"><FolderOpen size={16} /></div>
                            <div><strong>{text.saves}</strong><span>v{saveInventory?.version ?? "—"} · {saveInventory?.objects.length ?? 0} {saveToolLabels.object}</span></div>
                          </article>
                        </div>
                      </section>
                      <section className="save-tool-section">
                        <div className="save-tool-section-heading">
                          <div><strong>{saveToolLabels.trucks}</strong><span>{saveInventory?.trucks ?? 0} {saveToolLabels.detected}</span></div>
                          <span className="save-tool-count">{saveInventory?.trucks ?? 0}</span>
                        </div>
                        <div className="save-tool-card-grid">
                          {(saveInventory?.objects.filter((object) => object.kind === "truck").slice(0, 6) ?? []).map((object, index) => (
                            <article className="save-tool-card" key={`${object.structureName}-${index}`}>
                              <div className="save-tool-card-icon"><Wrench size={16} /></div>
                              <div><strong>{object.structureName}</strong><span>{object.fields.find((field) => field.name === "name")?.value || saveToolLabels.truckObject}</span></div>
                            </article>
                          ))}
                          {!saveInventory?.trucks && <div className="save-tool-empty">{text.noneFound}</div>}
                        </div>
                      </section>
                      <section className="save-tool-section">
                        <div className="save-tool-section-heading">
                          <div><strong>{saveToolLabels.trailers}</strong><span>{saveInventory?.trailers ?? 0} {saveToolLabels.detected}</span></div>
                          <span className="save-tool-count">{saveInventory?.trailers ?? 0}</span>
                        </div>
                        <div className="save-tool-card-grid">
                          {(saveInventory?.objects.filter((object) => object.kind === "trailer").slice(0, 6) ?? []).map((object, index) => (
                            <article className="save-tool-card" key={`${object.structureName}-${index}`}>
                              <div className="save-tool-card-icon"><FolderOpen size={16} /></div>
                              <div><strong>{object.structureName}</strong><span>{object.fields.find((field) => field.name === "name")?.value || saveToolLabels.trailerObject}</span></div>
                            </article>
                          ))}
                          {!saveInventory?.trailers && <div className="save-tool-empty">{text.noneFound}</div>}
                        </div>
                      </section>
                      {saveMutation && (
                        <div className={`save-transaction-notice ${saveMutation.success ? "is-success" : "is-neutral"}`}>
                          <strong>{saveMutation.message}</strong>
                          {saveMutation.backupPath && (
                            <span title={saveMutation.backupPath}>
                              {language === "zh_CN" ? "备份：" : language === "ru_RU" ? "Резервная копия: " : "Backup: "}
                              {saveMutation.backupPath.split(/[\\/]/).pop()}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                    <div className="save-form">
                      <div className="save-form-heading"><strong>{text.saveSnapshot}</strong><span>{saveSnapshot ? `${text.saveSnapshot} · v${saveSnapshot.version}` : text.scanning}</span></div>
                      {saveSnapshot?.fields.length ? (
                        <>
                          {(["money_account", "experience_points"] as const).map((fieldName) => {
                            const field = saveSnapshot.fields.find((entry) => entry.fieldName === fieldName);
                            if (!field) return null;
                            const minimum = fieldName === "money_account" ? Number.MIN_SAFE_INTEGER : 0;
                            const maximum = fieldName === "money_account" ? Number.MAX_SAFE_INTEGER : 0xFFFF_FFFF;
                            const draft = saveValues[fieldName];
                            return (
                              <div className="save-form-row" key={fieldName}>
                                <div><strong>{fieldName === "money_account" ? text.money : text.experience}</strong><small>{field.value.toLocaleString()}</small></div>
                                <input type="number" value={draft} onChange={(event) => updateSaveDraft(fieldName, event.target.value)} placeholder={String(field.value)} disabled={selectedSave.profileLocation !== "local" || loading} />
                                <button className="button button-primary button-small" disabled={selectedSave.profileLocation !== "local" || loading || !isValidSaveDraft(draft, minimum, maximum)} onClick={() => submitSaveDraft(fieldName, fieldName === "money_account" ? "set_money" : "set_experience", minimum, maximum)}>{text.apply}</button>
                              </div>
                            );
                          })}
                          <div className="save-form-row">
                            <div><strong>{text.level}</strong><small>{levelFromExperience(saveSnapshot.fields.find((entry) => entry.fieldName === "experience_points")?.value ?? 0).toLocaleString()}</small></div>
                            <input type="number" min="1" max="200" value={saveValues.level} onChange={(event) => updateSaveDraft("level", event.target.value)} placeholder="1-200" disabled={selectedSave.profileLocation !== "local" || loading} />
                            <button className="button button-primary button-small" disabled={selectedSave.profileLocation !== "local" || loading || !isValidSaveDraft(saveValues.level, 1, 200)} onClick={() => submitSaveDraft("level", "set_level", 1, 200)}>{text.apply}</button>
                          </div>
                        </>
                      ) : <div className="detail-empty compact"><FolderOpen size={24} /><span>{selectedSave.profileLocation === "local" ? text.noneFound : text.saveReadOnly}</span></div>}
                    </div>
                  </>
                ) : (
                  <div className="detail-empty"><FolderOpen size={25} /><strong>{text.noSelection}</strong><span>{text.saveSelectHint}</span></div>
                )}
              </section>
            </div>
          </section>
        </main>
      ) : activePage === "profiles" ? (
        <main className="profile-page">
          <section className="profile-page-main">
            <div className="page-heading">
              <div>
                <div className="panel-title">{text.profileOverview}</div>
                <div className="panel-subtitle">{text.profiles} · {profiles.length}</div>
              </div>
              <button className="button button-primary" onClick={() => setActivePage("mods")}><LayoutGrid size={16} />{text.openModPage}</button>
            </div>
            <div className="profile-card-grid">
              {profiles.map((profile) => (
                <button
                  key={profile.id}
                  className={`profile-card ${profile.id === selectedProfileId ? "is-selected" : ""}`}
                  onClick={() => { void selectProfile(profile.id); }}
                  onContextMenu={(event) => {
                    event.preventDefault();
                    setProfileContextMenu({
                      profileId: profile.id,
                      x: Math.min(event.clientX, window.innerWidth - 260),
                      y: Math.max(8, Math.min(event.clientY, window.innerHeight - 330)),
                    });
                  }}
                >
                  <span className="profile-card-icon"><FolderOpen size={19} /></span>
                  <span className="profile-card-copy">
                    <strong>{profile.name}</strong>
                    <small>{profile.company || profile.location}</small>
                  </span>
                  <span className="profile-card-meta">
                    <b>{profile.id === selectedProfileId ? activeCount : profile.modCount}</b>
                    <small>{text.activeMods}</small>
                  </span>
                </button>
              ))}
            </div>
            <div className="profile-detail-grid">
              <section className="profile-info-panel">
                <div className="section-heading"><span>{text.profileDetails}</span></div>
                <dl className="detail-meta profile-meta">
                  <div><dt>{text.name}</dt><dd>{selectedProfile.name}</dd></div>
                  <div><dt>{text.author}</dt><dd>{selectedProfile.company || "—"}</dd></div>
                  <div><dt>{text.profileStatus}</dt><dd>{selectedProfile.writable === false ? text.readOnly : text.localProfile}</dd></div>
                  <div><dt>{text.profileFolder}</dt><dd title={selectedProfile.folder || ""}>{selectedProfile.folder || "—"}</dd></div>
                  <div><dt>{text.activeMods}</dt><dd>{text.statusCount(activeCount, mods.length)}</dd></div>
                </dl>
              </section>
              <section className="profile-info-panel">
                <div className="section-heading"><span>{text.profileActions}</span></div>
                <div className="profile-action-list">
                  <button className="button" onClick={() => { setActivePage("mods"); setSecondaryPanel("saves"); }}><FolderOpen size={15} />{text.saves}</button>
                  <button className="button" onClick={() => { setActivePage("localization"); setSecondaryPanel("localization"); }}><Sparkles size={15} />{text.localization}</button>
                  <button className="button" onClick={() => { setActivePage("mods"); setSecondaryPanel("diagnostics"); void runDiagnostics(); }}><LayoutGrid size={15} />{text.diagnostics}</button>
                </div>
                <div className="profile-stat-line"><span>{text.saves}</span><strong>{saves.length}</strong></div>
                <div className="profile-stat-line"><span>{text.presetPlaceholder}</span><strong>{text.profilePresetCount(Object.keys(presets).length)}</strong></div>
              </section>
            </div>
          </section>
        </main>
      ) : (
      <main className="workspace">
        <aside className="sidebar">
          <section className="sidebar-section">
            <div className="section-heading"><span>{text.profiles}</span><span className="section-count">{profiles.length}</span></div>
            <div className="profile-list">
              {profiles.map((profile) => (
                <button
                  key={profile.id}
                  className={`profile-item ${profile.id === selectedProfileId ? "is-selected" : ""}`}
                  onClick={() => { void selectProfile(profile.id); }}
                  onContextMenu={(event) => {
                    event.preventDefault();
                    setProfileContextMenu({
                      profileId: profile.id,
                      x: Math.min(event.clientX, window.innerWidth - 260),
                      y: Math.max(8, Math.min(event.clientY, window.innerHeight - 330)),
                    });
                  }}
                >
                  <span className="profile-icon"><FolderOpen size={15} /></span>
                  <span className="profile-copy">
                    <strong>{profile.name}</strong>
                    <small>{profile.company}</small>
                  </span>
                  <span className="profile-count">
                    {profile.id === selectedProfileId ? mods.filter((mod) => mod.enabled).length : profile.modCount}
                  </span>
                </button>
              ))}
            </div>
          </section>
          <CategorySidebar onCategory={chooseCategory} />
          <section className="sidebar-secondary">
            <div className="section-heading"><span>{text.secondary}</span></div>
            <button className={`secondary-item ${secondaryPanel === "localization" ? "is-selected" : ""}`} onClick={() => { setSecondaryPanel("localization"); }}>
              <Sparkles size={15} />{text.localization}<span className="secondary-count">{localization?.entries.length ?? 0}</span>
            </button>
            <button className={`secondary-item ${secondaryPanel === "diagnostics" ? "is-selected" : ""}`} onClick={() => { setSecondaryPanel("diagnostics"); void runDiagnostics(); }}>
              <LayoutGrid size={15} />{text.diagnostics}<span className="secondary-count">{diagnostics?.redCount ?? 0}</span>
            </button>
            <button className={`secondary-item ${secondaryPanel === "saves" ? "is-selected" : ""}`} onClick={() => setSecondaryPanel(secondaryPanel === "saves" ? "none" : "saves")}>
              <FolderOpen size={15} />{text.saves}<span className="secondary-count">{saves.length}</span>
            </button>
            <button className="secondary-item" disabled><Wrench size={15} />{text.tools}</button>
          </section>
        </aside>

        <section className="mod-panel">
          {error && <div className="error-banner" role="alert">{categoryError(error, language)}</div>}
          <div className="panel-toolbar">
            <div>
              <div className="panel-title">{text.modWorkspace}</div>
              <div className="panel-subtitle">{selectedProfile.name} · {text.statusCount(activeCount, mods.length)}{selectedProfile.writable === false ? ` · ${text.readOnly}` : ""}</div>
            </div>
          </div>

          <div className="view-row">
            <div className="segmented-control">
              <button className={view === "all" ? "is-active" : ""} onClick={() => { setView("all"); setBatchScope("filtered"); }}>{text.allMods}</button>
              <button className={view === "active" ? "is-active" : ""} onClick={() => { setView("active"); setBatchScope("filtered"); }}>{text.activeMods}</button>
            </div>
            <div className="search-box">
              <Search size={16} />
              <input value={query} onChange={(event) => { setQuery(event.target.value); setBatchScope("filtered"); }} placeholder={text.searchPlaceholder} />
              <select aria-label={searchMode === "exact" ? text.searchExact : text.searchFuzzy} value={searchMode} onChange={(event) => setSearchMode(event.target.value as SearchMode)}>
                <option value="fuzzy">{text.searchFuzzy}</option>
                <option value="exact">{text.searchExact}</option>
              </select>
            </div>
          </div>

          <div className="batch-toolbar">
            <select className="batch-scope" value={batchScope} aria-label={categoryText.scope} onChange={(event) => setBatchScope(event.target.value as typeof batchScope)}>
              <option value="selection">{categoryText.selection} ({selectedModIds.length})</option>
              <option value="filtered">{categoryText.filtered} ({visibleIds.length})</option>
              <option value="category" disabled={selectedCategory === ALL_CATEGORIES}>{categoryText.category} ({categoryIds.length})</option>
            </select>
            <button className="button button-small" onClick={() => batchMods(batchIds, "enable")} disabled={!canBatch}><Check size={14} />{categoryText.enable}</button>
            <button className="button button-small" onClick={() => batchMods(batchIds, "disable")} disabled={!canBatch}><X size={14} />{categoryText.disable}</button>
            <button className="icon-button" title={categoryText.invert} aria-label={categoryText.invert} onClick={() => batchMods(batchIds, "invert")} disabled={!canBatch}><CheckSquare size={16} /></button>
            <span className="toolbar-divider" />
            <label className="category-assign"><FolderInput size={16} /><select aria-label={categoryText.assign} value="" disabled={categoryBusy || loading || scanning || !batchIds.length} onChange={(event) => {
              if (event.target.value) void mutateCategory({ operation: "assign", name: event.target.value === "\0none" ? "" : event.target.value, modIds: batchIds });
            }}>
              <option value="" disabled>{categoryText.assign}</option>
              {categoryState.folders.map((name) => <option value={name} key={name}>{name}</option>)}
            </select></label>
            <button className="icon-button" title={categoryText.clear} aria-label={categoryText.clear} disabled={!selectedModIds.length} onClick={() => selectMods([])}><X size={15} /></button>
          </div>
          <div className="priority-row">
            <span className="priority-label">{text.priority}</span>
            <button className="icon-button" onClick={() => moveSelected("top")} title={text.moveTop} disabled={!canMove}><ArrowUpToLine size={16} /></button>
            <button className="icon-button" onClick={() => moveSelected("up")} title={text.moveUp} disabled={!canMove}><ArrowUp size={16} /></button>
            <select className="move-steps" value={moveSteps} aria-label={categoryText.steps} title={categoryText.steps} onChange={(event) => setMoveSteps(Number(event.target.value))}>{[1, 10, 50, 100].map((n) => <option key={n}>{n}</option>)}</select>
            <button className="icon-button" onClick={() => moveSelected("down")} title={text.moveDown} disabled={!canMove}><ArrowDown size={16} /></button>
            <button className="icon-button" onClick={() => moveSelected("bottom")} title={text.moveBottom} disabled={!canMove}><ArrowDownToLine size={16} /></button>
          </div>
          <div className="preset-row">
            <input className="preset-input" value={presetName} onChange={(event) => setPresetName(event.target.value)} placeholder={text.presetPlaceholder} />
            <button className="button button-small" disabled={!presetName.trim() || selectedProfile.writable === false} onClick={() => { void savePreset(presetName); setPresetName(""); }}>{text.savePreset}</button>
            <select className="preset-select" value={selectedPresetName} onChange={(event) => selectPreset(event.target.value)} aria-label={text.presetSelectPlaceholder} disabled={!selectedProfile.writable}>
              <option value="">{text.presetSelectPlaceholder}</option>
              {Object.keys(presets).map((name) => <option value={name} key={name}>{name}</option>)}
            </select>
            <button className="button button-small" disabled={!selectedPresetName || !selectedProfile.writable} onClick={() => { void loadPreset(); }}>{text.loadPreset}</button>
          </div>

          <div className="table-wrap" ref={tableWrapRef}>
            <table className="mod-table">
              <colgroup><col className="selection-column" /><col className="enabled-column" /><col /><col className="category-column" /><col className="source-column" /><col className="package-column" /></colgroup>
              <thead><tr><th className="selection-column"><TriCheckbox checked={visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id))}
                partial={visibleIds.some((id) => selectedIds.has(id)) && !visibleIds.every((id) => selectedIds.has(id))}
                label={categoryText.selectVisible} onChange={() => { selectMods(visibleIds.every((id) => selectedIds.has(id)) ? [] : visibleIds); setBatchScope("selection"); }} /></th>
                <th className="check-column">{text.enabled}</th><th>{text.name}</th><th>{text.category}</th><th>{text.source}</th><th>{text.package}</th></tr></thead>
              <tbody>
                {filteredMods.map((mod) => (
                  <tr
                    key={mod.id}
                    data-mod-id={mod.id}
                    draggable={!loading && !categoryBusy}
                    onDragStart={(event) => {
                      const ids = selectedIds.has(mod.id) ? selectedModIds : [mod.id];
                      event.dataTransfer.setData("text/mod-id", mod.id);
                      event.dataTransfer.setData("application/x-ets2-mod-ids", JSON.stringify(ids));
                      selectMods(ids, mod.id); setBatchScope("selection");
                    }}
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={(event) => onDrop(mod.id, event)}
                    className={selectedIds.has(mod.id) ? "is-selected" : mod.id === selectedModId ? "is-focused" : ""}
                    aria-selected={selectedIds.has(mod.id)}
                    onClick={(event) => selectRow(mod.id, event)}
                    onContextMenu={(event) => {
                      event.preventDefault();
                      selectMods([mod.id], mod.id);
                      setContextMoveOpen(false);
                      setContextCategoryOpen(false);
                      setContextMenu({
                        modId: mod.id,
                        x: Math.min(event.clientX, window.innerWidth - 250),
                        y: Math.max(8, Math.min(event.clientY, window.innerHeight - 360)),
                      });
                    }}
                  >
                    <td className="selection-column"><TriCheckbox checked={selectedIds.has(mod.id)} label={`${categoryText.select}: ${mod.displayName}`} onChange={() => {
                      selectMods(selectedIds.has(mod.id) ? selectedModIds.filter((id) => id !== mod.id) : [...selectedModIds, mod.id], mod.id);
                      selectionAnchor.current = mod.id; setBatchScope("selection");
                    }} /></td>
                    <td className="check-column"><button className={`toggle ${mod.enabled ? "is-on" : ""}`} disabled={loading || !selectedProfile.writable} onClick={(event) => { event.stopPropagation(); toggleMod(mod.id); }} aria-label={text.enabled}>{mod.enabled && <Check size={14} />}</button></td>
                    <td><div className="mod-name-cell"><ModThumbnail mod={mod} /><span><strong>{mod.displayName}</strong><small>{mod.author}</small></span></div></td>
                    <td><span className="tag" title={mod.category || categoryText.uncategorized}>{mod.category || categoryText.uncategorized}</span></td>
                    <td><span className={`source source-${mod.source}`}>{mod.source === "local" ? text.sourceLocal : text.sourceWorkshop}</span></td>
                    <td className="package-cell">{mod.packageName}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {filteredMods.length === 0 && <div className="empty-state">{text.noMods}</div>}
          </div>
          {contextMenu && contextMod && <div className="mod-context-menu" style={{ left: contextMenu.x, top: contextMenu.y }} onClick={(event) => event.stopPropagation()} onWheel={(event) => event.stopPropagation()}>
            <div className="context-menu-title">{contextMod.displayName}</div>
            <div className="context-menu-group">
              <button onClick={() => { setContextMoveOpen((value) => !value); setContextCategoryOpen(false); }}><ArrowUp size={14} />{text.priority}<ChevronDown size={13} /></button>
              {contextMoveOpen && <div className="context-submenu">
                {([["top", text.moveTop], ["up", text.moveUp], ["down", text.moveDown], ["bottom", text.moveBottom]] as const).map(([direction, label]) => (
                  <button key={direction} onClick={() => { moveMods([contextMod.id], direction, 1); setContextMenu(null); }}>{label}</button>
                ))}
                {[2, 5, 10, 25].map((steps) => <button key={`up-${steps}`} onClick={() => { moveMods([contextMod.id], "up", steps); setContextMenu(null); }}>{text.moveUp} {steps}</button>)}
                {[2, 5, 10, 25].map((steps) => <button key={`down-${steps}`} onClick={() => { moveMods([contextMod.id], "down", steps); setContextMenu(null); }}>{text.moveDown} {steps}</button>)}
              </div>}
            </div>
            <button onClick={() => { batchMods([contextMod.id], "enable"); setContextMenu(null); }} disabled={contextMod.enabled}><Check size={14} />{categoryText.enable}</button>
            <button onClick={() => { batchMods([contextMod.id], "disable"); setContextMenu(null); }} disabled={!contextMod.enabled}><X size={14} />{categoryText.disable}</button>
            <button onClick={() => { void openModLocation(contextMod.id); setContextMenu(null); }}><FolderOpen size={14} />{contextMod.source === "workshop" ? text.openWorkshop : text.openLocation}</button>
            <div className="context-menu-group">
              <button onClick={() => { setContextCategoryOpen((value) => !value); setContextMoveOpen(false); }}><FolderInput size={14} />{categoryText.assign}<ChevronDown size={13} /></button>
              {contextCategoryOpen && <div className="context-submenu">
                {categoryState.folders.map((name) => <button key={name} onClick={() => { void mutateCategory({ operation: "assign", name, modIds: [contextMod.id] }); setContextMenu(null); }}>{name}</button>)}
              </div>}
            </div>
          </div>}

          <div className="panel-status">
            <span className={`status-dot ${dirty ? "dirty" : ""}`} />
            {scanWasCancelled ? text.scanCancelled : dirty ? text.statusDirty : text.statusReady}
            {scanSummary && !scanWasCancelled && <span className="scan-summary">{text.scanSummary(scanSummary.added, scanSummary.updated, scanSummary.removed, scanSummary.inspected)}</span>}
            <span className="status-spacer" />{text.statusCount(activeCount, mods.length)}
          </div>
        </section>

        <aside className="details-panel">
          {secondaryPanel === "saves" ? (
            <div className="save-panel">
              <div className="save-panel-heading">
                <div>
                  <div className="detail-title">{text.saves}</div>
                  <div className="detail-package">{text.saveCount(saves.length)}</div>
                </div>
                <FolderOpen size={18} />
              </div>
              {saves.length === 0 ? (
                <div className="detail-empty compact"><FolderOpen size={24} /><span>{text.noSaves}</span></div>
              ) : (
                <div className="save-list">
                  {saves.map((save) => (
                    <div
                      className={`save-item ${selectedSave?.slotId === save.slotId ? "is-selected" : ""} ${save.slotId.toLowerCase().startsWith("autosave") ? "is-autosave" : ""}`}
                      key={save.slotId}
                      role="button"
                      tabIndex={0}
                      aria-pressed={selectedSave?.slotId === save.slotId}
                      onClick={() => openSaveEditor(save)}
                      onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openSaveEditor(save); } }}
                    >
                      <div className="save-item-icon"><FolderOpen size={15} /></div>
                      <div className="save-item-copy">
                        <strong>{save.displayName}</strong>
                        <small>
                          {save.slotId.toLowerCase().startsWith("autosave") ? text.autosave : text.saveUpdated}
                          {" · "}
                          {formatSaveDate(save.lastModifiedMs)}
                        </small>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              <div className="save-hint">{text.saveSelectHint}</div>
              {selectedSave && (
                <div className="save-editor">
                  <div className="save-editor-heading">
                    <strong>{selectedSave.displayName}</strong>
                    <span>{saveSnapshot ? text.saveSnapshot : text.scanning}</span>
                  </div>
                  {saveSnapshot?.fields.length ? (
                    <>
                      {(["money_account", "experience_points"] as const).map((fieldName) => {
                        const field = saveSnapshot.fields.find((entry) => entry.fieldName === fieldName);
                        if (!field) return null;
                        const minimum = fieldName === "money_account" ? Number.MIN_SAFE_INTEGER : 0;
                        const maximum = fieldName === "money_account" ? Number.MAX_SAFE_INTEGER : 0xFFFF_FFFF;
                        const draft = saveValues[fieldName];
                        return (
                          <div className="save-field" key={fieldName}>
                            <div><span>{fieldName === "money_account" ? text.money : text.experience}</span><small>{field.value.toLocaleString()}</small></div>
                            <input
                              type="number"
                              value={draft}
                              onChange={(event) => updateSaveDraft(fieldName, event.target.value)}
                              placeholder={String(field.value)}
                              disabled={selectedSave.profileLocation !== "local" || loading}
                            />
                            <button
                              className="button button-small"
                              disabled={selectedSave.profileLocation !== "local" || loading || !isValidSaveDraft(draft, minimum, maximum)}
                              onClick={() => submitSaveDraft(fieldName, fieldName === "money_account" ? "set_money" : "set_experience", minimum, maximum)}
                            >
                              {text.apply}
                            </button>
                          </div>
                        );
                      })}
                      <div className="save-field">
                        <div><span>{text.level}</span><small>{levelFromExperience(saveSnapshot.fields.find((entry) => entry.fieldName === "experience_points")?.value ?? 0).toLocaleString()}</small></div>
                        <input type="number" min="1" max="200" value={saveValues.level} onChange={(event) => updateSaveDraft("level", event.target.value)} placeholder="1-200" disabled={selectedSave.profileLocation !== "local" || loading} />
                        <button className="button button-small" disabled={selectedSave.profileLocation !== "local" || loading || !isValidSaveDraft(saveValues.level, 1, 200)} onClick={() => submitSaveDraft("level", "set_level", 1, 200)}>{text.apply}</button>
                      </div>
                    </>
                  ) : (
                    <div className="save-hint">{selectedSave.profileLocation === "local" ? text.noneFound : text.saveReadOnly}</div>
                  )}
                </div>
              )}
            </div>
          ) : secondaryPanel === "localization" ? (
            <div className="save-panel">
              <div className="save-panel-heading">
                <div>
                  <div className="detail-title">{text.localization}</div>
                  <div className="detail-package">{localization ? text.entriesSummary(localization.entries.length, localization.cached, localization.inspected) : text.noSelection}</div>
                </div>
                <Sparkles size={18} />
              </div>
              <button className="button button-primary panel-action" onClick={() => { void (localizationScanning ? cancelLocalization() : scanLocalization()); }} disabled={scanning || (!localizationScanning && !localizationBase)}>
                <Sparkles size={14} />{localizationScanning ? text.cancelScan : text.scanLocalization}
              </button>
              <div className={`localization-progress ${localizationScanning ? "is-active" : ""}`}>
                <div className="localization-progress-track"><span /></div>
                {localizationScanning ? (
                  <div className="localization-progress-copy">
                    <span className="localization-progress-mod" title={localizationFileProgress?.packageName ?? localizationProgress?.packageName ?? ""}>
                      {text.scanning}: {localizationFileProgress?.packageName || localizationProgress?.packageName || "..."}
                    </span>
                    <span className="localization-progress-file" title={localizationFileProgress?.file ?? ""}>
                      {text.localizationFile}: {localizationFileProgress?.file || "..."}
                    </span>
                    <span className="localization-progress-count">
                      {localizationProgress?.processed ?? 0} / {localizationProgress?.total ?? 0}
                    </span>
                  </div>
                ) : (
                  <span>{localization ? text.entriesSummary(localization.entries.length, localization.cached, localization.inspected) : text.noSelection}</span>
                )}
              </div>
              <button className="button button-small panel-action" disabled={!localization?.entries.length || localizationScanning} onClick={saveLocalizationAsClick}>
                <Save size={13} />{text.saveLocalizationAs}
              </button>
              <div className="localization-base-control">
                <div className="detail-package" title={localizationBase ?? ""}>
                  {text.localizationBase}: {localizationBase ? localizationBase.split(/[\\/]/).pop() : text.noneFound}
                </div>
                <button className="button button-small" onClick={() => { void pickLocalizationBase(); }}>
                  <FolderOpen size={14} />{text.chooseLocalizationBase}
                </button>
              </div>
              {localization?.entries.length ? (
                <div className="support-list">
                  {localization.entries.slice(0, 80).map((entry) => (
                    <div className="support-row" key={`${entry.packageName}:${entry.key}`}>
                      <span className="support-kind">{localizationCategory(entry.category, language)}</span>
                      <div className="support-key">
                        <strong>{entry.sourceName || entry.key}</strong>
                        <code>@@{entry.key}@@</code>
                      </div>
                      <input value={localizationDraft[entry.key] ?? entry.value} placeholder={entry.value || "—"} onChange={(event) => setLocalizationDraft((current) => ({ ...current, [entry.key]: event.target.value }))} />
                    </div>
                  ))}
                </div>
              ) : <div className="detail-empty compact"><Sparkles size={24} /><span>{text.noneFound}</span></div>}
            </div>
          ) : secondaryPanel === "diagnostics" ? (
            <div className="save-panel">
              <div className="save-panel-heading">
                <div>
                  <div className="detail-title">{text.diagnostics}</div>
                  <div className="detail-package">{diagnostics ? text.issueSummary(diagnostics.redCount, diagnostics.yellowCount) : text.noSelection}</div>
                </div>
                <LayoutGrid size={18} />
              </div>
              <button className="button button-small panel-action" onClick={() => { void runDiagnostics(); }} disabled={loading || scanning}>{text.scan}</button>
              {diagnostics && (
                <div className="diagnostic-log-summary">
                  <div className="detail-package">{text.logSummary}</div>
                  <p>{diagnostics.logSummary || text.noneFound}</p>
                  <div className="diagnostic-paths">
                    {diagnostics.crashPath && <code>{text.crashReport}: {diagnostics.crashPath}</code>}
                    {diagnostics.logPath && <code>{text.gameLog}: {diagnostics.logPath}</code>}
                  </div>
                  {diagnostics.logEvidence?.length ? (
                    <div className="diagnostic-evidence">
                      <div className="support-kind">{text.logEvidence}</div>
                      {diagnostics.logEvidence.slice(0, 12).map((line) => <code key={line}>{line}</code>)}
                    </div>
                  ) : null}
                </div>
              )}
              {diagnostics?.issues.length ? (
                <div className="support-list">
                  {diagnostics.issues.map((issue) => (
                    <div className={`support-row issue-${issue.severity}`} key={`${issue.code}:${issue.modId}:${issue.priorityIndex ?? 0}`}>
                      <div><strong>{issue.displayName}</strong><small>{issue.code}</small></div>
                      <span>{issue.evidence}</span>
                    </div>
                  ))}
                </div>
              ) : <div className="detail-empty compact"><LayoutGrid size={24} /><span>{text.noneFound}</span></div>}
            </div>
          ) : selectedMod ? (
            <>
              <div className="detail-art"><ModImage src={selectedMod.previewUrl || selectedMod.iconUrl} fallback={selectedMod.iconUrl} className="detail-image" alt={selectedMod.displayName} /><span>{selectedMod.category || categoryText.uncategorized}</span></div>
              <div className="detail-heading"><div className="detail-title">{selectedMod.displayName}</div><span className={`source source-${selectedMod.source}`}>{selectedMod.source === "local" ? text.sourceLocal : text.sourceWorkshop}</span></div>
              <div className="detail-package">{selectedMod.packageName}</div>
              <p className="detail-description">{selectedMod.description}</p>
              <dl className="detail-meta">
                <div><dt>{text.author}</dt><dd>{selectedMod.author}</dd></div>
                <div><dt>{text.version}</dt><dd>{selectedMod.version}</dd></div>
                <div><dt>{text.compatible}</dt><dd>{selectedMod.compatible}</dd></div>
                <div><dt>{text.category}</dt><dd>{selectedMod.category || categoryText.uncategorized}</dd></div>
              </dl>
            </>
          ) : (
            <div className="detail-empty"><Sparkles size={25} /><strong>{text.noSelection}</strong><span>{text.selectHint}</span></div>
          )}
        </aside>
      </main>
      )}
      {profileContextMenu && contextProfile && (
        <div
          className="mod-context-menu profile-context-menu"
          style={{ left: profileContextMenu.x, top: profileContextMenu.y }}
          onClick={(event) => event.stopPropagation()}
        >
          <div className="context-menu-title">{contextProfile.name}</div>
          <button onClick={() => { void selectProfile(contextProfile.id); setProfileContextMenu(null); }}>
            <Check size={14} />{text.profileSwitch}
          </button>
          <button onClick={() => { void selectProfile(contextProfile.id); setActivePage("profiles"); setProfileContextMenu(null); }}>
            <FolderOpen size={14} />{text.profileOpenManager}
          </button>
          <button onClick={() => { void selectProfile(contextProfile.id); setActivePage("mods"); setProfileContextMenu(null); }}>
            <LayoutGrid size={14} />{text.profileOpenMods}
          </button>
          <div className="context-menu-group">
            <button onClick={() => { void openProfilePanel(contextProfile.id, "saves"); setProfileContextMenu(null); }}>
              <FolderOpen size={14} />{text.profileOpenSaves}
            </button>
            <button onClick={() => { void openProfilePanel(contextProfile.id, "localization"); setProfileContextMenu(null); }}>
              <Languages size={14} />{text.profileOpenLocalization}
            </button>
            <button onClick={() => { void openProfilePanel(contextProfile.id, "diagnostics"); setProfileContextMenu(null); }}>
              <Wrench size={14} />{text.profileOpenDiagnostics}
            </button>
          </div>
          <button
            disabled={!contextProfile.folder}
            onClick={() => { void openProfileLocation(contextProfile.id); setProfileContextMenu(null); }}
          >
            <ExternalLink size={14} />{text.profileOpenFolder}
          </button>
        </div>
      )}
      {directoryOpen && <ModDirectoryDialog onClose={() => setDirectoryOpen(false)} />}
    </div>
  );
}

export default App;
