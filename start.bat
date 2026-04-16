@echo off
cd /d c:\tools\linkedin-intel
echo Starting LinkedIn Intel API...
start cmd /k "python api.py"
timeout /t 2 /nobreak >nul
echo Opening dashboard...
start dashboard.html
