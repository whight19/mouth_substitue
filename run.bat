@echo off
chcp 65001 >nul
cd /d "%~dp0."
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "%~dp0app.py"
    exit /b 0
)
where pythonw.exe >nul 2>nul
if errorlevel 1 (
    echo 还没有安装运行环境。请先双击 install.bat。
    pause
    exit /b 1
)
start "" pythonw.exe "%~dp0app.py"

