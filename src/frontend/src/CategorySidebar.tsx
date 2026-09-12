import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowDownToLine, ArrowUp, ArrowUpToLine, Check, FolderInput, MoreHorizontal, Pencil, Plus, Trash2, X } from "lucide-react";
import { useModStore } from "./store";
import { getCopy } from "./i18n";
import { categoryCopy, categoryError } from "./categoryI18n";
import { ALL_CATEGORIES } from "./modBatch";

export function TriCheckbox({ checked, partial = false, label, disabled, onChange }: {
  checked: boolean; partial?: boolean; label: string; disabled?: boolean; onChange: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { if (ref.current) ref.current.indeterminate = partial; }, [partial]);
  return <input ref={ref} type="checkbox" className="selection-checkbox" checked={checked} disabled={disabled}
    aria-label={label} title={label} onChange={onChange} onClick={(event) => event.stopPropagation()} />;
}

export function readDraggedMods(event: React.DragEvent): string[] {
  try {
    const parsed: unknown = JSON.parse(event.dataTransfer.getData("application/x-ets2-mod-ids"));
    if (Array.isArray(parsed) && parsed.every((id) => typeof id === "string")) return parsed;
  } catch { /* Older one-row drags still work. */ }
  const id = event.dataTransfer.getData("text/mod-id");
  return id ? [id] : [];
}

export default function CategorySidebar({ onCategory }: { onCategory: (category: string) => void }) {
  const { language, mods, selectedCategory, selectedModIds, categoryState, categoryBusy, loading, scanning,
    selectedProfileId, profiles, batchMods, moveMods, mutateCategory, error } = useModStore();
  const text = getCopy(language);
  const copy = categoryCopy[language];
  const [menu, setMenu] = useState<string | null>(null);
  const [editor, setEditor] = useState<{ operation: "create" | "rename" | "delete"; name: string } | null>(null);
  const [name, setName] = useState("");
  const [steps, setSteps] = useState(1);
  const dialog = useRef<HTMLDialogElement>(null);
  const root = useRef<HTMLElement>(null);
  const writable = !loading && profiles.some((p) => p.id === selectedProfileId && p.writable !== false);
  const categoryLocked = categoryBusy || loading || scanning;
  const groupedMods = useMemo(() => {
    const groups = new Map<string, typeof mods>();
    for (const mod of mods) {
      const group = groups.get(mod.category);
      if (group) group.push(mod);
      else groups.set(mod.category, [mod]);
    }
    return groups;
  }, [mods]);
  useEffect(() => {
    if (editor) dialog.current?.showModal();
  }, [editor]);
  useEffect(() => {
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setMenu(null); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setMenu(null); };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, []);
  function edit(operation: "create" | "rename" | "delete", category = "") {
    useModStore.setState({ error: "" });
    setName(category);
    setMenu(null);
    setEditor({ operation, name: category });
  }
  async function saveCategory() {
    if (!editor) return;
    const ok = await mutateCategory({ ...editor, name: editor.operation === "create" ? name.trim() : editor.name, newName: name.trim() });
    if (ok) setEditor(null);
  }
  return <section className="sidebar-section category-section" ref={root}>
    <div className="section-heading"><span>{text.categories}</span>
      <button className="icon-button" title={copy.create} aria-label={copy.create} disabled={categoryLocked} onClick={() => edit("create")}><Plus size={16} /></button>
    </div>
    {categoryState.warning && <p className="category-warning" title={categoryState.warning}>{copy.importWarning}</p>}
    <button className={`category-item ${selectedCategory === ALL_CATEGORIES ? "is-selected" : ""}`} onClick={() => { onCategory(ALL_CATEGORIES); setMenu(null); }}>
      <span className="category-dot dot-all" />{text.allMods}<span>{mods.length}</span>
    </button>
    {["", ...categoryState.folders].map((category) => {
      const items = groupedMods.get(category) ?? [];
      const enabled = items.filter((mod) => mod.enabled).length;
      const ids = items.map((mod) => mod.id);
      const label = category || copy.uncategorized;
      return <div key={category} className={`category-folder ${selectedCategory === category ? "is-selected" : ""}`}
        onDragOver={(event) => { if (!categoryLocked) { event.preventDefault(); event.dataTransfer.dropEffect = "move"; } }}
        onDrop={(event) => { event.preventDefault(); const modIds = readDraggedMods(event); if (modIds.length) void mutateCategory({ operation: "assign", name: category, modIds }); }}
        onContextMenu={(event) => { event.preventDefault(); setMenu(category); }}>
        <TriCheckbox checked={items.length > 0 && enabled === items.length} partial={enabled > 0 && enabled < items.length}
          label={`${copy.categoryToggle}: ${label}`} disabled={!writable || !items.length} onChange={() => batchMods(ids, enabled === items.length ? "disable" : "enable")} />
        <button className="category-label" onClick={() => { onCategory(category); setMenu(null); }} title={label}>
          <span>{label}</span><small>{enabled}/{items.length}</small>
        </button>
        <button className="icon-button" aria-label={`${copy.actions}: ${label}`} title={copy.actions} aria-expanded={menu === category}
          onClick={() => setMenu(menu === category ? null : category)}><MoreHorizontal size={15} /></button>
        {menu === category && <div className="category-menu" role="menu" aria-label={`${copy.actions}: ${label}`}>
          <button role="menuitem" disabled={categoryLocked || !selectedModIds.length} onClick={() => { void mutateCategory({ operation: "assign", name: category, modIds: selectedModIds }); setMenu(null); }}><FolderInput size={14} />{copy.categoryAssign}</button>
          <button role="menuitem" disabled={!writable || !items.length} onClick={() => { batchMods(ids, "enable"); setMenu(null); }}><Check size={14} />{copy.enable}</button>
          <button role="menuitem" disabled={!writable || !items.length} onClick={() => { batchMods(ids, "disable"); setMenu(null); }}><X size={14} />{copy.disable}</button>
          <button role="menuitem" disabled={!writable || !items.length} onClick={() => { batchMods(ids, "invert"); setMenu(null); }}>{copy.invert}</button>
          <label>{copy.steps}<select value={steps} onChange={(event) => setSteps(Number(event.target.value))}>{[1, 10, 50, 100].map((n) => <option key={n}>{n}</option>)}</select></label>
          {([["top", text.moveTop, ArrowUpToLine], ["up", text.moveUp, ArrowUp], ["down", text.moveDown, ArrowDown], ["bottom", text.moveBottom, ArrowDownToLine]] as const).map(([direction, title, Icon]) => (
            <button role="menuitem" key={direction} disabled={!writable || !enabled} onClick={() => { moveMods(ids, direction, steps); setMenu(null); }}><Icon size={14} />{title}</button>
          ))}
          {category && <>
            <button role="menuitem" disabled={categoryLocked} onClick={() => edit("rename", category)}><Pencil size={14} />{copy.rename}</button>
            <button role="menuitem" disabled={categoryLocked} onClick={() => edit("delete", category)}><Trash2 size={14} />{copy.delete}</button>
          </>}
        </div>}
      </div>;
    })}
    {editor && <dialog ref={dialog} className="directory-dialog category-editor" onCancel={(event) => { event.preventDefault(); if (!categoryBusy) setEditor(null); }} aria-labelledby="category-editor-title">
      <header className="directory-heading"><h2 id="category-editor-title">{copy[editor.operation]}</h2></header>
      <form onSubmit={(event) => { event.preventDefault(); void saveCategory(); }}>
        <div className="directory-body">
          {editor.operation === "delete" ? <p>{copy.deleteConfirm}<br /><strong>{editor.name}</strong></p> : <>
            <label className="directory-target" htmlFor="category-name">{copy.name}</label>
            <div className="directory-input"><input id="category-name" autoFocus value={name} maxLength={80} disabled={categoryBusy} onChange={(event) => setName(event.target.value)} /></div>
          </>}
          {error && <p className="directory-error" role="alert">{categoryError(error, language)}</p>}
        </div>
        <footer className="directory-actions">
          <button type="button" className="button" disabled={categoryBusy} onClick={() => setEditor(null)}>{copy.cancel}</button>
          <button className="button button-primary" disabled={categoryBusy || (editor.operation !== "delete" && !name.trim())}>{copy.confirm}</button>
        </footer>
      </form>
    </dialog>}
  </section>;
}
