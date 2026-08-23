@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   ETS2 Mod Manager  ?  ??????2 ?????
echo ============================================================
echo.
echo [1/2] ?? PySide6?
python -c "import PySide6" 2>nul
if errorlevel 1 (
    echo   [!] ??? PySide6????? pip install --user PySide6
    python -m pip install --user PySide6
)
echo.
echo [2/2] ?? UI?
python run.py
if errorlevel 1 (
    echo.
    echo [!] ???????????????????
    pause
)
