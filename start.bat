@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   ETS2 Mod Manager  -  欧洲卡车模拟2 模组管理器
echo ============================================================
echo.

echo [1/3] 检查 Python (pythonw.exe)...
where pythonw >nul 2>nul
if errorlevel 1 (
    echo   [!] 未找到 pythonw.exe
    echo   请确认已安装 Python 并将 pythonw.exe 所在目录加入 PATH
    echo   或直接运行打包后的 dist\ETS2ModManager.exe
    pause
    exit /b 1
)

echo [2/3] 检查 PySide6...
python -c "import PySide6" 2>nul
if errorlevel 1 (
    echo   [!] 未安装 PySide6，正在安装 pip install --user PySide6
    python -m pip install --user PySide6
)

echo [3/3] 启动 UI（后台无终端运行）...
:: pythonw.exe 无控制台窗口；start 让 cmd 立即返回；exit 关闭 cmd 窗口
:: 用户前端不会再看到任何黑色终端窗口
start "" pythonw run.py
exit