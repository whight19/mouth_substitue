@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0."

echo ==========================================
echo   嘴替 MouthSubstitute - 安装依赖
echo ==========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 没有找到 Python。请先安装 Python 3.10 或更高版本，
    echo        安装时勾选 "Add python.exe to PATH"。
    echo        下载地址：https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo [错误] Python 版本太低，需要 3.10 或更高版本。
    python --version
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] 创建独立虚拟环境 .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败。
        pause
        exit /b 1
    )
) else (
    echo [1/3] 已存在 .venv，直接复用。
)

echo.
echo [2/3] 安装依赖（首次需要联网，大约 30-50 MB）...
if not defined PIP_INDEX_URL set "PIP_INDEX_URL=https://pypi.org/simple"
echo       使用软件源：%PIP_INDEX_URL%
> ".venv\pip.ini" echo [global]
>> ".venv\pip.ini" echo index-url = %PIP_INDEX_URL%
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败。常见原因：
    echo        1) 当前软件源不可用，换一个源后重试：
    echo           set PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
    echo           install.bat
    echo        2) .venv 里的 pip 损坏（提示 No module named pip）：
    echo           .venv\Scripts\python.exe -m ensurepip --upgrade
    echo           然后重新运行 install.bat
    pause
    exit /b 1
)

echo.
echo [3/3] 安装完成。
echo.
echo 双击 run.bat 启动程序。
echo 如果听不到声音，先运行 run_console.bat 查看日志，
echo 或在界面里点“刷新设备”“测试声音”。
echo.
pause
