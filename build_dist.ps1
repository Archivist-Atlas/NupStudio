# Build standalone Windows distribution directly in dist\ (no Python required for end users)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

$distRoot = Join-Path $root "dist"
$tmpWork = Join-Path $root "_pyinstaller_work"
$stageDist = Join-Path $tmpWork "stage"

if (Test-Path -LiteralPath $distRoot) { Remove-Item -Recurse -Force $distRoot }
if (Test-Path -LiteralPath $tmpWork) { Remove-Item -Recurse -Force $tmpWork }
New-Item -ItemType Directory -Force -Path $distRoot | Out-Null

Write-Host "[1/3] Building NUP Studio.exe (onedir, _internal in dist\_internal)..." -ForegroundColor Cyan
python -m PyInstaller `
    --noconfirm --clean --onedir --windowed `
    --name "NUP Studio" `
    --contents-directory "dist/_internal" `
    --icon (Join-Path $root "assets\app_icon.ico") `
    --distpath $stageDist `
    --workpath (Join-Path $tmpWork "main") `
    --specpath $tmpWork `
    --paths "tools\packagers" `
    --hidden-import "nup" `
    --hidden-import "nup.cli" `
    --hidden-import "nup.discover" `
    --hidden-import "nup.pack" `
    --hidden-import "nup.unpack" `
    --hidden-import "zstandard" `
    --hidden-import "PIL.Image" `
    "main.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$builtExe = Join-Path $stageDist "NUP Studio\NUP Studio.exe"
$builtInternal = Join-Path $stageDist "NUP Studio\dist\_internal"
Move-Item -LiteralPath $builtExe -Destination (Join-Path $root "NUP Studio.exe") -Force
Move-Item -LiteralPath $builtInternal -Destination (Join-Path $distRoot "_internal") -Force

Write-Host "[2/3] Building standalone tools\game_launcher.exe (onefile)..." -ForegroundColor Cyan
python -m PyInstaller `
    --noconfirm --clean --onefile --windowed `
    --name "game_launcher" `
    --icon (Join-Path $root "assets\app_icon.ico") `
    --paths (Join-Path $root "src") `
    --paths (Join-Path $root "tools\packagers") `
    --hidden-import "nup" `
    --hidden-import "nup.access" `
    --hidden-import "nup.read" `
    --hidden-import "zstandard" `
    --distpath (Join-Path $root "tools") `
    --workpath (Join-Path $tmpWork "launcher") `
    --specpath $tmpWork `
    "src\game_launcher.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[3/3] Cleaning temporary PyInstaller work files..." -ForegroundColor Cyan
if (Test-Path -LiteralPath $tmpWork) { Remove-Item -Recurse -Force $tmpWork }

Write-Host "Build complete: $distRoot" -ForegroundColor Green
