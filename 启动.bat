@echo off
setlocal
title Liao Xiao Zhi
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
  goto :done
)
if exist ".venv\Scripts\python.exe" (
  start "" ".venv\Scripts\python.exe" "%~dp0main.py"
  goto :done
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0main.py"
) else (
  start "" python "%~dp0main.py"
)

:done
endlocal
