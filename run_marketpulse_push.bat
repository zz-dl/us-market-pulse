@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
"D:\Program\python.exe" -u push_marketpulse.py >> marketpulse_push.log 2>&1
