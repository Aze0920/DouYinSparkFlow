@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动 DouYinSparkFlow 控制台...
echo 浏览器打开 http://127.0.0.1:8787
echo 默认密码: sparkflow
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [错误] 没找到 Python，请先安装 Python 3.10+
  pause
  exit /b 1
)

if not exist config mkdir config
if not exist logs mkdir logs
if not exist config\.env copy /Y .env.example config\.env >nul

python -m pip install -r requirements.txt
python -m webui.app
pause
