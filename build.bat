@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   ?? ETS2 Mod Manager  (PyInstaller onefile)
echo ============================================================
where pyinstaller >nul 2>nul
if errorlevel 1 (
    echo [!] ??? PyInstaller??????
    python -m pip install --user pyinstaller pyside6
)
echo.
echo ?????  ???????? ~3-7 ???
pyinstaller --noconfirm --clean --onefile --windowed ^
    --name "ETS2ModManager" ^
    --collect-submodules PySide6 ^
    --collect-all PySide6 ^
    run.py
if errorlevel 1 (
    echo [!] ????
    exit /b 1
)
echo.
echo ? ????????dist\ETS2ModManager.exe
pause
