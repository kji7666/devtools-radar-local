@echo off
cd /d D:\side_project\auto_gpt

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

call .\.venv\Scripts\activate.bat
python api_server.py --host 127.0.0.1 --port 8788
pause