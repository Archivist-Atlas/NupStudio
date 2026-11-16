@echo off
setlocal
cd /d "%~dp0"

if exist "%~dp0dist\NUP Studio.exe" (
    start "" "%~dp0dist\NUP Studio.exe" %*
    exit /b 0
)

