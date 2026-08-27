"""启动入口：python -m src  或  python src/__main__.py"""
from ui.main_window import main
from version import __version__, __app_name__

if __name__ == "__main__":
    main()
