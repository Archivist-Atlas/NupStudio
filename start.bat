@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Python est introuvable dans le PATH. Installez Python 3.10+ puis relancez start.bat.
    pause
    exit /b 1
)

python -c "import PySide6, zstandard, PIL" >nul 2>&1
if errorlevel 1 (
    echo Installation des dependances requises ^(requirements.txt^)...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERREUR] Echec de l'installation des dependances.
        pause
        exit /b 1
    )
)

python main.py %*
if errorlevel 1 pause
