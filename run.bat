@echo off
cd /d D:\side_project\auto_gpt

if not exist logs mkdir logs
if not exist screenshots mkdir screenshots
if not exist outputs mkdir outputs
if not exist tasks mkdir tasks
if not exist archive mkdir archive
if not exist failed mkdir failed
if not exist scheduled_templates mkdir scheduled_templates

D:\side_project\auto_gpt\.venv\Scripts\python.exe main.py --batch >> logs\bat.log 2>&1