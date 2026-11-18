# Global MSYS2 (Clang64) installer for NUP Studio / Switch2Recomp
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File install_msys2.ps1 [-Msys C:\msys64] [-Yes]
param(
    [string]$Msys = "C:\msys64",
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}
function Say($msg) { Write-Host "[MSYS2] $msg" -ForegroundColor Cyan }

if (-not [Environment]::Is64BitOperatingSystem) {
    Say "Erreur : Windows 64 bits est requis pour MSYS2."
    exit 1
}

$bash = Join-Path $Msys "usr\bin\bash.exe"
$clang64 = Join-Path $Msys "clang64\bin"
$requiredFiles = @(
    $bash,
    (Join-Path $Msys "usr\bin\make.exe"),
    (Join-Path $clang64 "clang.exe"),
    (Join-Path $clang64 "clang++.exe"),
    (Join-Path $clang64 "lld.exe"),
    (Join-Path $clang64 "cmake.exe"),
    (Join-Path $clang64 "ninja.exe"),
    (Join-Path $clang64 "python.exe"),
    (Join-Path $Msys "clang64\include\capstone\capstone.h"),
    (Join-Path $Msys "clang64\include\SDL3\SDL.h"),
    (Join-Path $Msys "clang64\include\vpx\vpx_decoder.h"),
    (Join-Path $Msys "clang64\include\vma\vk_mem_alloc.h")
)

$allReady = $true
foreach ($req in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $req)) {
        $allReady = $false
        break
    }
}

if ($allReady) {
    Say "MSYS2 (Clang64) est d$([char]0x00E9)j$([char]0x00E0) install$([char]0x00E9) et pr$([char]0x00EA)t dans $Msys."
    exit 0
}

if (-not $Yes) {
    $confirmed = $false
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $res = [System.Windows.Forms.MessageBox]::Show(
            "MSYS2 (Clang64) est n$([char]0x00E9)cessaire pour recompiler ce jeu et va $([char]0x00EA)tre install$([char]0x00E9) sur votre lecteur C ($Msys).`n`nSouhaitez-vous installer MSYS2 et les outils de compilation maintenant ?`n(Si vous refusez, la recompilation sera arr$([char]0x00EA)t$([char]0x00E9)e.)",
            "Installation requise : MSYS2",
            [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Question
        )
        $confirmed = ($res -eq [System.Windows.Forms.DialogResult]::Yes)
    } catch {
        $ans = Read-Host "MSYS2 va $([char]0x00EA)tre install$([char]0x00E9) dans $Msys. Continuer ? [O/n]"
        $confirmed = ($ans -eq "" -or $ans -match "^[OoYy]")
    }
    if (-not $confirmed) {
        Say "Installation de MSYS2 refus$([char]0x00E9)e par l'utilisateur. Recompilation annul$([char]0x00E9)e."
        exit 1
    }
}

if (-not (Test-Path -LiteralPath $bash)) {
    Say "Installation de MSYS2 dans $Msys..."
    $installed = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id MSYS2.MSYS2 -e --silent --accept-package-agreements --accept-source-agreements --location $Msys
        $installed = Test-Path -LiteralPath $bash
    }
    if (-not $installed) {
        $setup = Join-Path $env:TEMP "msys2-installer.exe"
        Say "T$([char]0x00E9)l$([char]0x00E9)chargement de l'installeur MSYS2..."
        Invoke-WebRequest "https://repo.msys2.org/distrib/msys2-x86_64-latest.exe" -OutFile $setup
        & $setup in --confirm-command --accept-messages --root ($Msys -replace '\\', '/') | Out-Null
        Remove-Item -LiteralPath $setup -Force -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path -LiteralPath $bash)) {
        Say "Erreur : impossible d'installer MSYS2 dans $Msys."
        exit 1
    }
}

function Invoke-Msys($cmd) {
    $env:MSYSTEM = "CLANG64"
    $env:CHERE_INVOKING = "1"
    & $bash -lc $cmd
    if ($LASTEXITCODE -ne 0) {
        throw "$([char]0x00C9)chec de la commande MSYS2 : $cmd"
    }
}

Say "Mise $([char]0x00E0) jour de MSYS2 et installation des outils Clang64 (cela peut prendre quelques minutes la premi$([char]0x00E8)re fois)..."
& $bash -lc "pacman -Syu --noconfirm" | Out-Host
& $bash -lc "pacman -Syu --noconfirm" | Out-Host

$packages = "clang lld cmake ninja python python-numpy python-lz4 python-capstone pkgconf sdl3 opus ffmpeg libvpx vulkan-loader vulkan-headers vulkan-memory-allocator shaderc spirv-tools fmt zstd lz4 xxhash" -split " " |
    ForEach-Object { "mingw-w64-clang-x86_64-$_" }
Invoke-Msys ("pacman -S --needed --noconfirm make " + ($packages -join " "))

Say "MSYS2 (Clang64) install$([char]0x00E9) et configur$([char]0x00E9) avec succ$([char]0x00E8)s dans $Msys."
exit 0
