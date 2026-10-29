<#
.SYNOPSIS
  Build PMAC Doctor: run the tests, freeze with PyInstaller and produce a Windows installer.

.DESCRIPTION
  Steps
    1. create/refresh the virtual environment (.venv) with requirements-dev.txt
    2. run the unit tests (skip with -SkipTests)
    3. PyInstaller one-folder build          -> dist\PmacDoctor\PmacDoctor.exe
    4. portable zip                          -> dist\PmacDoctor-<ver>-portable.zip
    5. Inno Setup installer (if ISCC found)  -> dist\installer\PmacDoctor-<ver>-setup.exe

  Inno Setup is looked for in tools\InnoSetup (portable install created by this script from
  tools\downloads\innosetup-*.exe with /PORTABLE=1), then in the usual Program Files locations.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File build\build.ps1
#>
param(
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [switch]$Clean
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$version = (Select-String -Path "pmacdoctor\__init__.py" -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
Write-Host "== PMAC Doctor $version build ==" -ForegroundColor Cyan

# ---------------------------------------------------------------- 1. environment
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "-- creating virtual environment"
    python -m venv ".venv"
    & $py -m pip install --upgrade pip
}
& $py -m pip install -q -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

if ($Clean) {
    Remove-Item -Recurse -Force "dist", (Join-Path $env:LOCALAPPDATA "PmacDoctor-build") -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------- 2. tests
if (-not $SkipTests) {
    Write-Host "-- running unit tests"
    & $py -m pytest tests\test_core.py -q
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }
}

# ---------------------------------------------------------------- 3. PyInstaller
Write-Host "-- freezing with PyInstaller"
& $py tools\make_icon.py | Out-Null
# The frozen application is built OUTSIDE OneDrive: the sync client locks files while they are
# being written, which breaks PyInstaller's cleanup and any mirror copy. Only the two
# deliverables (portable zip and installer) are written back into the project.
$work = Join-Path $env:LOCALAPPDATA "PmacDoctor-build"
$appdir = Join-Path $work "dist\PmacDoctor"
& $py -m PyInstaller "build\pmacdoctor.spec" --noconfirm --workpath (Join-Path $work "work") --distpath (Join-Path $work "dist")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$exe = Join-Path $appdir "PmacDoctor.exe"
if (-not (Test-Path $exe)) { throw "PyInstaller did not produce $exe" }
Write-Host "   -> $exe"
New-Item -ItemType Directory -Force "dist" | Out-Null

# ---------------------------------------------------------------- 4. portable zip
$zip = Join-Path $root "dist\PmacDoctor-$version-portable.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $appdir "*") -DestinationPath $zip -CompressionLevel Optimal
Write-Host "   -> $zip"

# ---------------------------------------------------------------- 5. installer
if ($SkipInstaller) { Write-Host "-- installer skipped"; exit 0 }
$iscc = $null
$candidates = @(
    (Join-Path $root "tools\InnoSetup\ISCC.exe"),
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 7\ISCC.exe",
    "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe",
    "${env:LOCALAPPDATA}\Programs\Inno Setup 7\ISCC.exe"
)
foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { $iscc = $c; break } }
if (-not $iscc) {
    $inst = Get-ChildItem "tools\downloads\innosetup-*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($inst) {
        Write-Host "-- installing Inno Setup portably into tools\InnoSetup"
        $target = Join-Path $root "tools\InnoSetup"
        Start-Process -FilePath $inst.FullName -ArgumentList "/PORTABLE=1", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=`"$target`"" -Wait
        if (Test-Path (Join-Path $target "ISCC.exe")) { $iscc = Join-Path $target "ISCC.exe" }
    }
}
if (-not $iscc) {
    Write-Warning "Inno Setup (ISCC.exe) not found. Download innosetup-6.x.exe into tools\downloads and re-run, or install Inno Setup. The portable zip is ready."
    exit 0
}
Write-Host "-- compiling installer with $iscc"
New-Item -ItemType Directory -Force "dist\installer" | Out-Null
& $iscc /Qp "/DSourceDir=$appdir" "/DOutputDir=$root\dist\installer" "installer\PmacDoctor.iss"
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }
Get-ChildItem "dist\installer\*.exe" | ForEach-Object { Write-Host "   -> $($_.FullName)  ($([math]::Round($_.Length/1MB,1)) MB)" }
Write-Host "   unpacked application (not synced): $appdir"
Write-Host "== done ==" -ForegroundColor Green
