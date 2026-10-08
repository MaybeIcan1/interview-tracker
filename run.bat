@echo off
chcp 65001 >nul
cd /d "D:\AIDigest\interview-tracker"
if not exist logs mkdir logs
"D:\anaconda3\python.exe" tracker.py check >> "logs\run.log" 2>&1
