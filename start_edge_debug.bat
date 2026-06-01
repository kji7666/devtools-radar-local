@echo off
set PROFILE_DIR=D:\side_project\auto_gpt\edge_debug_profile

start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="%PROFILE_DIR%" ^
  --no-first-run ^
  --no-default-browser-check ^
  --window-size=1280,900 ^
  https://chatgpt.com/