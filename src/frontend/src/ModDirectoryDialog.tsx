import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { listen } from "@tauri-apps/api/event";
import { FolderOpen, FolderInput, RotateCcw, Wrench, X } from "lucide-react";
import { isTauriRuntime } from "./backend";
import { useModStore } from "./store";
import type { Language } from "./types";

export const directoryCopy = {
  zh_CN: {
    title: "Mod 目录", gamePath: "游戏读取路径", actualPath: "实际存放位置",
    target: "新位置", browse: "选择文件夹", relocate: "迁移到新位置", restore: "还原到游戏目录",
    repair: "修复链接", recover: "恢复上次未完成的操作", close: "关闭", confirm: "确认操作",
    cancel: "返回", local: "本地目录", linked: "已重定向", broken: "链接失效", missing: "目录不存在",
    preflight: "检查文件", copy: "复制文件", verify: "校验文件", switch: "切换目录", complete: "已完成",
    busy: "正在处理", done: "Mod 目录已更新", retained: "保留的原文件或副本",
    saveFirst: "请先保存 Mod 排序和启用状态。", recovery: "上次目录操作被中断，请先恢复。",
    relocateConfirm: "将本地 Mod 复制并校验到新位置，再将游戏 mod 目录设为目录链接。目标必须为空；原文件保留为备份，不会自动删除。Workshop 不变。",
    restoreConfirm: "将 Mod 复制并校验回游戏原目录，再移除目录链接。原目标中的文件保留，不会自动删除。",
    repairConfirm: "将失效链接指向所选的现有 Mod 目录。不移动文件，完成后增量检查 Mod 索引。",
    recoverConfirm: "恢复中断操作之前的目录和缓存路径。已复制的数据保留，不会自动删除。",
  },
  en_US: {
    title: "Mod directory", gamePath: "Game directory", actualPath: "Actual location",
    target: "New location", browse: "Choose folder", relocate: "Relocate", restore: "Restore game directory",
    repair: "Repair link", recover: "Recover interrupted operation", close: "Close", confirm: "Confirm",
    cancel: "Back", local: "Local directory", linked: "Redirected", broken: "Broken link", missing: "Directory missing",
    preflight: "Checking files", copy: "Copying files", verify: "Verifying files", switch: "Switching directory", complete: "Complete",
    busy: "Working", done: "Mod directory updated", retained: "Retained originals or copies",
    saveFirst: "Save Mod order and enabled state first.", recovery: "A directory operation was interrupted. Recover it first.",
    relocateConfirm: "Copy and verify local Mods, then link the game mod directory to the new location. The target must be empty. Originals are retained as a backup, not deleted automatically. Workshop is unchanged.",
    restoreConfirm: "Copy and verify Mods back to the game directory, then remove the link. Files in the previous target are retained, not deleted automatically.",
    repairConfirm: "Point the broken link to the selected existing Mod directory. No files are moved. The Mod index will be checked incrementally.",
    recoverConfirm: "Restore the directory and cache paths from before the interrupted operation. Copied data is retained, not deleted automatically.",
  },
  ru_RU: {
    title: "Папка модов", gamePath: "Путь игры", actualPath: "Фактическое расположение",
    target: "Новое расположение", browse: "Выбрать папку", relocate: "Перенести", restore: "Вернуть в папку игры",
    repair: "Исправить ссылку", recover: "Восстановить прерванную операцию", close: "Закрыть", confirm: "Подтвердить",
    cancel: "Назад", local: "Локальная папка", linked: "Перенаправлено", broken: "Ссылка недоступна", missing: "Папка отсутствует",
    preflight: "Проверка файлов", copy: "Копирование файлов", verify: "Проверка копий", switch: "Смена папки", complete: "Готово",
    busy: "Выполняется", done: "Папка модов обновлена", retained: "Сохранённые оригиналы или копии",
    saveFirst: "Сначала сохраните порядок и состояние модов.", recovery: "Операция была прервана. Сначала выполните восстановление.",
    relocateConfirm: "Скопировать и проверить локальные моды, затем создать ссылку из папки игры. Новая папка должна быть пустой. Оригиналы сохраняются как резервная копия и не удаляются автоматически. Workshop не меняется.",
    restoreConfirm: "Скопировать и проверить моды в папке игры, затем удалить ссылку. Файлы в прежней папке сохраняются и не удаляются автоматически.",
    repairConfirm: "Направить неработающую ссылку на выбранную папку модов. Файлы не перемещаются. Затем выполняется инкрементальная проверка индекса.",
    recoverConfirm: "Восстановить папку и пути кеша до прерванной операции. Скопированные данные сохраняются и не удаляются автоматически.",
  },
} satisfies Record<Language, Record<string, string>>;

interface DirectoryStatus {
  gamePath: string;
  actualPath: string;
  kind: "local" | "linked" | "broken" | "missing";
  recoveryPending: boolean;
}
interface DirectoryProgress {
  phase: "preflight" | "copy" | "verify" | "switch" | "complete";
  path: string;
  completed: number;
  total: number;
}
type Operation = "relocate" | "restore" | "repair" | "recover";

export default function ModDirectoryDialog({ onClose }: { onClose: () => void }) {
  const { language, dirty, initialize, scan } = useModStore();
  const text = directoryCopy[language];
  const dialog = useRef<HTMLDialogElement>(null);
  const busyRef = useRef(false);
  const [status, setStatus] = useState<DirectoryStatus | null>(null);
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [progress, setProgress] = useState<DirectoryProgress | null>(null);
  const [confirm, setConfirm] = useState<Operation | null>(null);
  const refresh = () => invoke<DirectoryStatus>("mod_directory_status").then(setStatus);

  useEffect(() => {
    dialog.current?.showModal();
    void refresh().catch((e) => setError(String(e)));
    let disposed = false;
    const cleanups: Array<() => void> = [];
    const register = (cleanup: () => void) => { if (disposed) cleanup(); else cleanups.push(cleanup); };
    void listen<DirectoryProgress>("ets2-directory-progress", (event) => setProgress(event.payload))
      .then(register).catch((e) => setError(String(e)));
    if (isTauriRuntime()) {
      void getCurrentWindow().onCloseRequested((event) => { if (busyRef.current) event.preventDefault(); })
        .then(register).catch((e) => setError(String(e)));
    }
    return () => { disposed = true; cleanups.forEach((cleanup) => cleanup()); };
  }, []);

  async function browse() {
    setError("");
    try {
      const selected = await invoke<string | null>("mod_directory_pick");
      if (selected) setTarget(selected);
    } catch (reason) { setError(String(reason)); }
  }

  async function execute() {
    if (!confirm || busyRef.current) return;
    const operation = confirm;
    busyRef.current = true;
    setBusy(true);
    setConfirm(null);
    setError("");
    setNotice("");
    setProgress(null);
    try {
      const result = await invoke<{ status: DirectoryStatus; retainedPath?: string }>("mod_directory_change", {
        operation, target: target.trim(),
      });
      setStatus(result.status);
      setNotice(`${text.done}${result.retainedPath ? `\n${text.retained}: ${result.retainedPath}` : ""}`);
      // A relocation preserves the index; only a repaired link can reference a
      // different collection and needs an incremental scan.
      if (operation === "repair") await scan();
      await initialize();
      setTarget("");
    } catch (reason) {
      setError(String(reason));
      await refresh().catch(() => {});
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  const percent = progress?.total ? Math.min(100, Math.floor(progress.completed / progress.total * 100)) : null;
  return (
    <dialog ref={dialog} className="directory-dialog" aria-labelledby="directory-title" onCancel={(event) => {
      event.preventDefault();
      if (!busyRef.current) onClose();
    }}>
      <header className="directory-heading">
        <h2 id="directory-title"><FolderOpen size={19} />{text.title}</h2>
        <button className="icon-button" title={text.close} aria-label={text.close} disabled={busy} onClick={onClose}><X size={18} /></button>
      </header>
      <div className="directory-body">
        {status && <>
          <span className={`directory-kind ${status.kind === "broken" ? "is-broken" : ""}`}>{text[status.kind]}</span>
          <dl className="directory-paths">
            <dt>{text.gamePath}</dt><dd>{status.gamePath}</dd>
            <dt>{text.actualPath}</dt><dd>{status.actualPath}</dd>
          </dl>
        </>}
        {status?.recoveryPending && <p className="directory-warning">{text.recovery}</p>}
        {dirty && <p className="directory-warning">{text.saveFirst}</p>}
        <label className="directory-target" htmlFor="directory-target">{text.target}</label>
        <div className="directory-input">
          <input id="directory-target" value={target} onChange={(e) => setTarget(e.target.value)} disabled={busy || !!confirm || status?.recoveryPending} spellCheck={false} />
          <button className="icon-button" title={text.browse} aria-label={text.browse} onClick={() => void browse()} disabled={busy || !!confirm || status?.recoveryPending}><FolderOpen size={18} /></button>
        </div>
        {confirm && <div className="directory-confirm" role="alert">
          <p>{text[`${confirm}Confirm`]}</p>
          <strong>{confirm === "restore" ? status?.gamePath : confirm === "recover" ? status?.actualPath : target}</strong>
        </div>}
        {busy && <div className="directory-progress" role="status">
          <strong>{progress ? text[progress.phase] : text.busy}{percent !== null ? ` · ${percent}%` : ""}</strong>
          <progress className="initializer-progress" max={100} value={percent ?? undefined} />
          <span>{progress?.path ?? ""}</span>
        </div>}
        {notice && <p className="directory-notice" role="status">{notice}</p>}
        {error && <p className="directory-error" role="alert">{error}</p>}
      </div>
      <footer className="directory-actions">
        {confirm ? <>
          <button className="button" onClick={() => setConfirm(null)}>{text.cancel}</button>
          <button className="button button-primary" onClick={() => void execute()}>{text.confirm}</button>
        </> : status?.recoveryPending ? (
          <button className="button button-primary" disabled={busy} onClick={() => setConfirm("recover")}><RotateCcw size={16} />{text.recover}</button>
        ) : <>
          {status?.kind === "linked" && <button className="button" disabled={busy || dirty} onClick={() => setConfirm("restore")}><RotateCcw size={16} />{text.restore}</button>}
          <button className="button button-primary" disabled={busy || dirty || !status || status.kind === "missing" || !target.trim()} onClick={() => setConfirm(status?.kind === "broken" ? "repair" : "relocate")}>
            {status?.kind === "broken" ? <Wrench size={16} /> : <FolderInput size={16} />}{status?.kind === "broken" ? text.repair : text.relocate}
          </button>
        </>}
      </footer>
    </dialog>
  );
}
