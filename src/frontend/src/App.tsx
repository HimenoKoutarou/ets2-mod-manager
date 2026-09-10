import { useEffect, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowDownToLine,
  ArrowUp,
  ArrowUpToLine,
  Check,
  ChevronDown,
  FolderOpen,
  Languages,
  LayoutGrid,
  Play,
  RefreshCw,
  Save,
  Search,
  SlidersHorizontal,
  Sparkles,
  Wrench,
} from "lucide-react";
import { getCopy } from "./i18n";
import { useModStore } from "./store";

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
  const {
    language,
    profiles,
    selectedProfileId,
    mods,
    saves,
    selectedSave,
    saveSnapshot,
    localization,
    diagnostics,
    secondaryPanel,
    view,
    selectedCategory,
    query,
    selectedModId,
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
    setAll,
    invertAll,
    selectMod,
    moveMod,
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
    cancelLocalization,
    runDiagnostics,
    initialize,
    error,
  } = useModStore();
  const [presetName, setPresetName] = useState("");
  const [saveValues, setSaveValues] = useState<Record<SaveDraftKey, string>>({
    money_account: "",
    experience_points: "",
    level: "",
  });
  useEffect(() => {
    void initialize();
  }, []);
  useEffect(() => {
    setSaveValues({
      money_account: "",
      experience_points: "",
      level: "",
    });
  }, [selectedSave?.gameSii]);
  const text = getCopy(language);
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
      const matchesCategory = selectedCategory === "all" || mod.category === selectedCategory;
      if (!matchesView) return false;
      if (!matchesCategory) return false;
      if (!needle) return true;
      return [mod.displayName, mod.author, mod.packageName, mod.category].some((value) =>
        value.toLocaleLowerCase().includes(needle),
      );
    });
  }, [mods, query, selectedCategory, view]);
  const selectedMod = mods.find((mod) => mod.id === selectedModId) ?? null;
  const activeCount = mods.filter((mod) => mod.enabled).length;
  const formatSaveDate = (value: number) =>
    value > 0
      ? new Intl.DateTimeFormat(language.replace("_", "-"), {
          dateStyle: "medium",
          timeStyle: "short",
        }).format(new Date(value))
      : "—";

  function moveSelected(target: "top" | "up" | "down" | "bottom") {
    if (!selectedMod || !selectedMod.enabled) return;
    const activeMods = mods.filter((mod) => mod.enabled);
    const index = activeMods.findIndex((mod) => mod.id === selectedMod.id);
    const targetIndex =
      target === "top" ? 0 : target === "bottom" ? activeMods.length - 1 : target === "up" ? index - 1 : index + 1;
    moveMod(selectedMod.id, targetIndex);
  }

  function onDrop(id: string, event: React.DragEvent<HTMLTableRowElement>) {
    event.preventDefault();
    const targetMod = mods.find((mod) => mod.id === id);
    const sourceId = event.dataTransfer.getData("text/mod-id");
    if (!sourceId || !targetMod?.enabled) return;
    const target = mods.filter((mod) => mod.enabled).findIndex((mod) => mod.id === id);
    if (target >= 0) moveMod(sourceId, target);
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

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand-block">
          <div className="brand-mark"><Sparkles size={17} /></div>
          <div>
            <div className="brand-title">{text.appTitle}</div>
            <div className="brand-context">{selectedProfile.name} · {text.modWorkspace}</div>
          </div>
        </div>
        <div className="header-actions">
          <button className={`button ${scanning ? "button-warning" : "button-primary"}`} onClick={() => { void (scanning ? cancelScan() : scan()); }}>
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
                >
                  <span className="profile-icon"><FolderOpen size={15} /></span>
                  <span className="profile-copy">
                    <strong>{profile.name}</strong>
                    <small>{profile.company}</small>
                  </span>
                  <span className="profile-count">{profile.modCount}</span>
                </button>
              ))}
            </div>
          </section>
          <section className="sidebar-section">
            <div className="section-heading"><span>{text.categories}</span><SlidersHorizontal size={14} /></div>
            <button className={`category-item ${selectedCategory === "all" ? "is-selected" : ""}`} onClick={() => setCategory("all")}><span className="category-dot dot-all" />{text.allMods}<span>{mods.length}</span></button>
            {Array.from(new Set(mods.map((mod) => mod.category).filter((category) => category && category !== "unknown"))).sort((a, b) => a.localeCompare(b)).map((category) => (
              <button className={`category-item ${selectedCategory === category ? "is-selected" : ""}`} key={category} onClick={() => setCategory(category)}>
                <span className="category-dot" />{category}<span>{mods.filter((mod) => mod.category === category).length}</span>
              </button>
            ))}
          </section>
          <section className="sidebar-secondary">
            <div className="section-heading"><span>{text.secondary}</span></div>
            <button className={`secondary-item ${secondaryPanel === "localization" ? "is-selected" : ""}`} onClick={() => { setSecondaryPanel("localization"); void scanLocalization(); }}>
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
          {error && <div className="error-banner" role="alert">{error}</div>}
          <div className="panel-toolbar">
            <div>
              <div className="panel-title">{text.modWorkspace}</div>
              <div className="panel-subtitle">{selectedProfile.name} · {text.statusCount(activeCount, mods.length)}{selectedProfile.writable === false ? ` · ${text.readOnly}` : ""}</div>
            </div>
            <div className="batch-actions">
              <button className="button button-small" onClick={() => setAll(true)} disabled={!selectedProfile.writable}>{text.enableAll}</button>
              <button className="button button-small" onClick={() => setAll(false)} disabled={!selectedProfile.writable}>{text.disableAll}</button>
              <button className="button button-small" onClick={invertAll} disabled={!selectedProfile.writable}>{text.invert}</button>
            </div>
          </div>

          <div className="view-row">
            <div className="segmented-control">
              <button className={view === "all" ? "is-active" : ""} onClick={() => setView("all")}>{text.allMods}</button>
              <button className={view === "active" ? "is-active" : ""} onClick={() => setView("active")}>{text.activeMods}</button>
            </div>
            <div className="search-box"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={text.searchPlaceholder} /></div>
          </div>

          <div className="priority-row">
            <span className="priority-label">{text.priority}</span>
            <button className="icon-button" onClick={() => moveSelected("top")} title={text.moveTop} disabled={!selectedProfile.writable}><ArrowUpToLine size={16} /></button>
            <button className="icon-button" onClick={() => moveSelected("up")} title={text.moveUp} disabled={!selectedProfile.writable}><ArrowUp size={16} /></button>
            <button className="icon-button" onClick={() => moveSelected("down")} title={text.moveDown} disabled={!selectedProfile.writable}><ArrowDown size={16} /></button>
            <button className="icon-button" onClick={() => moveSelected("bottom")} title={text.moveBottom} disabled={!selectedProfile.writable}><ArrowDownToLine size={16} /></button>
            <span className="toolbar-divider" />
            <input className="preset-input" value={presetName} onChange={(event) => setPresetName(event.target.value)} placeholder={text.presetPlaceholder} />
            <button className="button button-small" disabled={!presetName.trim() || selectedProfile.writable === false} onClick={() => { void savePreset(presetName); setPresetName(""); }}>{text.savePreset}</button>
            <select className="preset-select" value={selectedPresetName} onChange={(event) => selectPreset(event.target.value)} aria-label={text.loadPreset} disabled={!selectedProfile.writable}>
              <option value="">{text.loadPreset}</option>
              {Object.keys(presets).map((name) => <option value={name} key={name}>{name}</option>)}
            </select>
            <button className="button button-small" disabled={!selectedPresetName || !selectedProfile.writable} onClick={() => { void loadPreset(); }}>{text.loadPreset}</button>
          </div>

          <div className="table-wrap">
            <table className="mod-table">
              <thead><tr><th className="check-column">{text.enabled}</th><th>{text.name}</th><th>{text.category}</th><th>{text.source}</th><th>{text.package}</th></tr></thead>
              <tbody>
                {filteredMods.map((mod, index) => (
                  <tr
                    key={mod.id}
                    draggable={selectedProfile.writable !== false && mod.enabled}
                    onDragStart={(event) => event.dataTransfer.setData("text/mod-id", mod.id)}
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={(event) => onDrop(mod.id, event)}
                    className={mod.id === selectedModId ? "is-selected" : ""}
                    onClick={() => selectMod(mod.id)}
                  >
                    <td className="check-column"><button className={`toggle ${mod.enabled ? "is-on" : ""}`} disabled={!selectedProfile.writable} onClick={(event) => { event.stopPropagation(); toggleMod(mod.id); }} aria-label={text.enabled}>{mod.enabled && <Check size={14} />}</button></td>
                    <td><div className="mod-name-cell"><span className={`mod-badge badge-${index % 4}`}><Sparkles size={14} /></span><span><strong>{mod.displayName}</strong><small>{mod.author}</small></span></div></td>
                    <td><span className="tag">{mod.category}</span></td>
                    <td><span className={`source source-${mod.source}`}>{mod.source === "local" ? text.sourceLocal : text.sourceWorkshop}</span></td>
                    <td className="package-cell">{mod.packageName}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {filteredMods.length === 0 && <div className="empty-state">{text.noMods}</div>}
          </div>

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
                      onClick={() => { void selectSave(save); }}
                      onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); void selectSave(save); } }}
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
              <button className="button button-small panel-action" onClick={() => { void (localizationScanning ? cancelLocalization() : scanLocalization()); }} disabled={scanning}>
                {localizationScanning ? text.cancelScan : text.scan}
              </button>
              {localization?.entries.length ? (
                <div className="support-list">
                  {localization.entries.slice(0, 80).map((entry) => (
                    <div className="support-row" key={`${entry.packageName}:${entry.key}`}>
                      <strong>{entry.key}</strong><span>{entry.value || "—"}</span>
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
              {diagnostics?.issues.length ? (
                <div className="support-list">
                  {diagnostics.issues.map((issue) => (
                    <div className={`support-row issue-${issue.severity}`} key={`${issue.code}:${issue.modId}:${issue.priorityIndex ?? 0}`}>
                      <strong>{issue.displayName}</strong><span>{issue.code}</span>
                    </div>
                  ))}
                </div>
              ) : <div className="detail-empty compact"><LayoutGrid size={24} /><span>{text.noneFound}</span></div>}
            </div>
          ) : selectedMod ? (
            <>
              <div className="detail-art"><Sparkles size={34} /><span>{selectedMod.category}</span></div>
              <div className="detail-heading"><div className="detail-title">{selectedMod.displayName}</div><span className={`source source-${selectedMod.source}`}>{selectedMod.source === "local" ? text.sourceLocal : text.sourceWorkshop}</span></div>
              <div className="detail-package">{selectedMod.packageName}</div>
              <p className="detail-description">{selectedMod.description}</p>
              <dl className="detail-meta">
                <div><dt>{text.author}</dt><dd>{selectedMod.author}</dd></div>
                <div><dt>{text.version}</dt><dd>{selectedMod.version}</dd></div>
                <div><dt>{text.compatible}</dt><dd>{selectedMod.compatible}</dd></div>
                <div><dt>{text.category}</dt><dd>{selectedMod.category}</dd></div>
              </dl>
            </>
          ) : (
            <div className="detail-empty"><Sparkles size={25} /><strong>{text.noSelection}</strong><span>{text.selectHint}</span></div>
          )}
        </aside>
      </main>
    </div>
  );
}

export default App;
