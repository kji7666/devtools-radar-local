@echo off
cd /d C:\project\devtools-radar-local

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

call .\.venv\Scripts\activate.bat

python -m uvicorn api_server:app --host 127.0.0.1 --port 8788 --loop asyncio

pause