import { useMemo, useState } from "react";
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
import { profiles, useModStore } from "./store";

function App() {
  const {
    language,
    selectedProfileId,
    mods,
    view,
    query,
    selectedModId,
    dirty,
    scanning,
    presets,
    selectedPresetName,
    setLanguage,
    selectProfile,
    setView,
    setQuery,
    toggleMod,
    setAll,
    invertAll,
    selectMod,
    moveMod,
    scan,
    save,
    savePreset,
    selectPreset,
    loadPreset,
  } = useModStore();
  const [presetName, setPresetName] = useState("");
  const text = getCopy(language);
  const selectedProfile = profiles.find((profile) => profile.id === selectedProfileId) ?? profiles[0];
  const filteredMods = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return mods.filter((mod) => {
      const matchesView = view === "all" || mod.enabled;
      if (!matchesView) return false;
      if (!needle) return true;
      return [mod.displayName, mod.author, mod.packageName, mod.category].some((value) =>
        value.toLocaleLowerCase().includes(needle),
      );
    });
  }, [mods, query, view]);
  const selectedMod = mods.find((mod) => mod.id === selectedModId) ?? null;
  const activeCount = mods.filter((mod) => mod.enabled).length;

  function moveSelected(target: "top" | "up" | "down" | "bottom") {
    if (!selectedMod) return;
    const index = mods.findIndex((mod) => mod.id === selectedMod.id);
    const targetIndex =
      target === "top" ? 0 : target === "bottom" ? mods.length - 1 : target === "up" ? index - 1 : index + 1;
    moveMod(selectedMod.id, targetIndex);
  }

  function onDrop(id: string, event: React.DragEvent<HTMLTableRowElement>) {
    event.preventDefault();
    const target = mods.findIndex((mod) => mod.id === id);
    const sourceId = event.dataTransfer.getData("text/mod-id");
    if (sourceId) moveMod(sourceId, target);
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
          <button className="button button-primary" onClick={scan} disabled={scanning}>
            <RefreshCw size={16} className={scanning ? "spin" : ""} />{scanning ? "…" : text.scan}
          </button>
          <button className="button button-primary" onClick={save} disabled={!dirty}><Save size={16} />{text.save}</button>
          <button className="button button-quiet" onClick={() => window.alert(text.launch)}><Play size={16} />{text.launch}</button>
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
                  onClick={() => selectProfile(profile.id)}
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
            <button className="category-item is-selected"><span className="category-dot dot-all" />{text.allMods}<span>{mods.length}</span></button>
            {["地图", "图形", "天气", "AI 交通", "声音"].map((category) => (
              <button className="category-item" key={category}>
                <span className="category-dot" />{category}<span>{mods.filter((mod) => mod.category === category).length}</span>
              </button>
            ))}
          </section>
          <section className="sidebar-secondary">
            <div className="section-heading"><span>{text.secondary}</span></div>
            <button className="secondary-item"><Sparkles size={15} />{text.localization}</button>
            <button className="secondary-item"><LayoutGrid size={15} />{text.diagnostics}</button>
            <button className="secondary-item"><FolderOpen size={15} />{text.saves}</button>
            <button className="secondary-item"><Wrench size={15} />{text.tools}</button>
          </section>
        </aside>

        <section className="mod-panel">
          <div className="panel-toolbar">
            <div>
              <div className="panel-title">{text.modWorkspace}</div>
              <div className="panel-subtitle">{selectedProfile.name} · {text.statusCount(activeCount, mods.length)}</div>
            </div>
            <div className="batch-actions">
              <button className="button button-small" onClick={() => setAll(true)}>{text.enableAll}</button>
              <button className="button button-small" onClick={() => setAll(false)}>{text.disableAll}</button>
              <button className="button button-small" onClick={invertAll}>{text.invert}</button>
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
            <span className="priority-label">{language === "zh_CN" ? "优先级" : language === "ru_RU" ? "Приоритет" : "Priority"}</span>
            <button className="icon-button" onClick={() => moveSelected("top")} title={text.moveTop}><ArrowUpToLine size={16} /></button>
            <button className="icon-button" onClick={() => moveSelected("up")} title={text.moveUp}><ArrowUp size={16} /></button>
            <button className="icon-button" onClick={() => moveSelected("down")} title={text.moveDown}><ArrowDown size={16} /></button>
            <button className="icon-button" onClick={() => moveSelected("bottom")} title={text.moveBottom}><ArrowDownToLine size={16} /></button>
            <span className="toolbar-divider" />
            <input className="preset-input" value={presetName} onChange={(event) => setPresetName(event.target.value)} placeholder={text.presetPlaceholder} />
            <button className="button button-small" disabled={!presetName.trim()} onClick={() => { savePreset(presetName); setPresetName(""); }}>{text.savePreset}</button>
            <select className="preset-select" value={selectedPresetName} onChange={(event) => selectPreset(event.target.value)} aria-label={text.loadPreset}>
              <option value="">{text.loadPreset}</option>
              {Object.keys(presets).map((name) => <option value={name} key={name}>{name}</option>)}
            </select>
            <button className="button button-small" disabled={!selectedPresetName} onClick={loadPreset}>{text.loadPreset}</button>
          </div>

          <div className="table-wrap">
            <table className="mod-table">
              <thead><tr><th className="check-column">{text.enabled}</th><th>NAME</th><th>{text.category}</th><th>{language === "zh_CN" ? "来源" : language === "ru_RU" ? "Источник" : "SOURCE"}</th><th>PACKAGE</th></tr></thead>
              <tbody>
                {filteredMods.map((mod, index) => (
                  <tr
                    key={mod.id}
                    draggable
                    onDragStart={(event) => event.dataTransfer.setData("text/mod-id", mod.id)}
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={(event) => onDrop(mod.id, event)}
                    className={mod.id === selectedModId ? "is-selected" : ""}
                    onClick={() => selectMod(mod.id)}
                  >
                    <td className="check-column"><button className={`toggle ${mod.enabled ? "is-on" : ""}`} onClick={(event) => { event.stopPropagation(); toggleMod(mod.id); }} aria-label={text.enabled}>{mod.enabled && <Check size={14} />}</button></td>
                    <td><div className="mod-name-cell"><span className={`mod-badge badge-${index % 4}`}><Sparkles size={14} /></span><span><strong>{mod.displayName}</strong><small>{mod.author}</small></span></div></td>
                    <td><span className="tag">{mod.category}</span></td>
                    <td><span className={`source source-${mod.source}`}>{mod.source === "local" ? text.sourceLocal : text.sourceWorkshop}</span></td>
                    <td className="package-cell">{mod.packageName}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {filteredMods.length === 0 && <div className="empty-state">{text.selectHint}</div>}
          </div>

          <div className="panel-status"><span className={`status-dot ${dirty ? "dirty" : ""}`} />{dirty ? text.statusDirty : text.statusReady}<span className="status-spacer" />{text.statusCount(activeCount, mods.length)}</div>
        </section>

        <aside className="details-panel">
          {selectedMod ? (
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
