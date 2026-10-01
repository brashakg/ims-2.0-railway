@echo off
cd /d "%~dp0\.."
".venv\Scripts\python.exe" "_pgmig\mongo_diag.py"
