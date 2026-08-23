"""双击运行：python run.py  （把 src 加到 sys.path 再启动 UI）"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from ui.main_window import main

if __name__ == "__main__":
    main()