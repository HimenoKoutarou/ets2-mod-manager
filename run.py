"""双击运行：python run.py  （把 src 加到 sys.path 再启动 UI）"""
import os
import sys
import traceback
from pathlib import Path


def _startup_log_path() -> Path:
    """Choose a persistent writable path for startup diagnostics."""
    base = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    candidates = [base / "logs" / "startup.log"]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "ETS2ModManager" / "startup.log")
    candidates.append(Path(os.environ.get("TEMP", ".")) / "ETS2ModManager-startup.log")
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
            return path
        except OSError:
            continue
    return candidates[-1]


_STARTUP_LOG = _startup_log_path()


def _write_startup_log(message: str) -> None:
    try:
        with _STARTUP_LOG.open("a", encoding="utf-8") as f:
            f.write(message.rstrip() + "\n")
    except OSError:
        pass


def _install_exception_logging() -> None:
    def _hook(exc_type, exc_value, exc_tb):
        _write_startup_log("[uncaught exception]\n" + "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))

    sys.excepthook = _hook
    try:
        import threading
        threading.excepthook = lambda args: _write_startup_log(
            "[thread exception]\n" + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        )
    except Exception:
        pass


_install_exception_logging()
_write_startup_log(
    f"\n[start] executable={sys.executable} frozen={getattr(sys, 'frozen', False)} cwd={os.getcwd()}"
)

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
try:
    # Keep this import at module scope so PyInstaller includes the UI package.
    from ui.main_window import main
except BaseException:
    _write_startup_log("[import failed]\n" + traceback.format_exc())
    raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        # A normal Qt event-loop return is not a startup failure.  Logging it
        # as one made real diagnostics needlessly confusing.
        raise
    except BaseException:
        _write_startup_log("[main failed]\n" + traceback.format_exc())
        raise
