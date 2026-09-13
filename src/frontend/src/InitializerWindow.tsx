import { useEffect, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import type { UnlistenFn } from "@tauri-apps/api/event";
import { RefreshCw, Sparkles } from "lucide-react";
import { initializeModIndex, type ScanProgress } from "./backend";

const messages = {
  zh: {
    stages: { cache: "打开持久化索引", local: "检查本地 Mod 文件", workshop: "检查 Workshop Mod 文件",
      metadata: "读取 Mod 元数据 / manifest", cached: "复用已有 Mod 缓存", persist: "提交 SQLite 索引",
      media: "提取并持久化 Mod 预览图",
      complete: "加载 Profile 和主窗口" },
    preparing: "正在准备初始化", retry: "重试", failed: "初始化失败",
    progress: (n: number, total: number) => `本阶段已处理 ${n} / ${total}`,
    working: "正在处理", elapsed: (n: number) => `当前操作已用时 ${n} 秒`,
  },
  en: {
    stages: { cache: "Opening persistent index", local: "Checking local mod files", workshop: "Checking Workshop mod files",
      metadata: "Reading mod metadata / manifest", cached: "Reusing cached mod metadata", persist: "Committing SQLite index",
      media: "Extracting and caching Mod previews",
      complete: "Loading profiles and main window" },
    preparing: "Preparing initialization", retry: "Retry", failed: "Initialization failed",
    progress: (n: number, total: number) => `Completed in this stage: ${n} / ${total}`,
    working: "Processing", elapsed: (n: number) => `Current operation: ${n} seconds`,
  },
  ru: {
    stages: { cache: "Открытие сохранённого индекса", local: "Проверка локальных модов", workshop: "Проверка модов Workshop",
      metadata: "Чтение метаданных / manifest", cached: "Повторное использование кэша", persist: "Сохранение индекса SQLite",
      media: "Извлечение и сохранение превью модов",
      complete: "Загрузка профилей и главного окна" },
    preparing: "Подготовка инициализации", retry: "Повторить", failed: "Ошибка инициализации",
    progress: (n: number, total: number) => `Обработано на этом этапе: ${n} / ${total}`,
    working: "Обработка", elapsed: (n: number) => `Текущая операция: ${n} сек.`,
  },
};

export default function InitializerWindow() {
  const locale = navigator.language.toLowerCase();
  const copy = messages[locale.startsWith("ru") ? "ru" : locale.startsWith("en") ? "en" : "zh"];
  const [progress, setProgress] = useState<ScanProgress | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [operationStarted, setOperationStarted] = useState(Date.now());
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    document.body.classList.add("initializer-body");
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      document.body.classList.remove("initializer-body");
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let active = true;
    let mainReady = false;
    let finished = false;
    let handedOff = false;
    const listeners: UnlistenFn[] = [];
    const current = getCurrentWindow();
    const fail = (reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : String(reason));
    };
    const finish = async () => {
      if (!active || !finished || !mainReady || handedOff) return;
      handedOff = true;
      await current.emitTo("main", "ets2-initialization-complete");
    };
    async function subscribe<T>(name: string, handler: (payload: T) => void) {
      const unlisten = await current.listen<T>(name, ({ payload }) => {
        if (active) handler(payload);
      });
      if (active) listeners.push(unlisten);
      else unlisten();
    }
    setError("");
    void (async () => {
      try {
        await subscribe<ScanProgress>("ets2-scan-progress", (value) => {
          setProgress(value);
          setOperationStarted(Date.now());
        });
        await subscribe("ets2-main-ready", () => {
          mainReady = true;
          void finish().catch(fail);
        });
        await subscribe<string>("ets2-initialization-error", fail);
        if (!active) return;
        const summary = await initializeModIndex();
        if (!active) return;
        finished = true;
        setProgress({ phase: "complete", current: summary.total, total: summary.total, name: "", path: "" });
        setOperationStarted(Date.now());
        await finish();
      } catch (reason) {
        fail(reason);
      }
    })();
    return () => {
      active = false;
      listeners.forEach((unlisten) => unlisten());
    };
  }, [attempt]);

  // Counts describe this stage, not a fabricated estimate of remaining time.
  const measurable = progress && progress.total > 0
    && ["local", "workshop", "metadata", "cached", "media"].includes(progress.phase);
  const percent = measurable ? Math.floor(100 * progress.current / progress.total) : undefined;
  return (
    <div className="initializer-shell">
      <div className="initializer-mark"><Sparkles size={22} /></div>
      <div className="initializer-title">ETS2 Mod Manager</div>
      <div className="initializer-stage" role="status">{error ? copy.failed : progress ? copy.stages[progress.phase] : copy.preparing}</div>
      <div className="initializer-current">
        <strong title={progress?.name}>{progress?.name || "\u00a0"}</strong>
        <div className="initializer-path" title={progress?.path}>{progress?.path || "\u00a0"}</div>
      </div>
      <progress className="initializer-progress" max={100} value={percent} aria-label={copy.working} />
      <div className="initializer-percent">
        {measurable ? `${copy.progress(progress.current, progress.total)} (${percent}%)` : copy.working}
      </div>
      <div className="initializer-status">{copy.elapsed(Math.max(0, Math.floor((now - operationStarted) / 1000)))}</div>
      {error && <div className="initializer-error" role="alert">{error}</div>}
      {error && <button className="button button-primary" onClick={() => setAttempt((value) => value + 1)}><RefreshCw size={14} />{copy.retry}</button>}
    </div>
  );
}
