@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo 关闭旧进程（如有）...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7860 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
echo 启动 Profit Protector（Web 版）...
python app.py
pause
