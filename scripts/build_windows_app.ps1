$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

if ($env:OS -ne "Windows_NT") {
    throw "Windows 应用只能在 Windows runner 上构建。"
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    py -3.12 -m venv (Join-Path $Root ".venv")
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements-dev.txt
& $Python -m pip install -r requirements-windows.txt
& $Python scripts/generate_windows_icon.py

npm --prefix dashboard-web ci --ignore-scripts --no-audit --no-fund
npm --prefix dashboard-web run test -- --pool=forks --maxWorkers=1
npm --prefix dashboard-web run lint
npm --prefix dashboard-web run build
& $Python -m unittest discover -s tests -v
& $Python scripts/check_licenses.py
node scripts/check_licenses.mjs
& $Python scripts/scan_secrets.py

Remove-Item build, dist -Recurse -Force -ErrorAction SilentlyContinue
& $Python -m PyInstaller --noconfirm --clean packaging/windows.spec

$Version = (& $Python -c "from sjtu_learning_assistant import __version__; print(__version__)").Trim()
$AppDir = Join-Path $Root "dist\SJTU Learning Assistant"
$Executable = Join-Path $AppDir "SJTU Learning Assistant.exe"
if (-not (Test-Path $Executable)) {
    throw "PyInstaller 未生成预期可执行文件：$Executable"
}

$Zip = Join-Path $Root "dist\SJTU-Learning-Assistant-$Version-Windows-x64-portable.zip"
Compress-Archive -Path "$AppDir\*" -DestinationPath $Zip -CompressionLevel Optimal -Force
(Get-FileHash $Zip -Algorithm SHA256).Hash.ToLowerInvariant() + "  " + (Split-Path $Zip -Leaf) |
    Set-Content "$Zip.sha256" -Encoding ascii

$IsccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
$IsccPath = if ($IsccCommand) { $IsccCommand.Source } else { $null }
if (-not $IsccPath) {
    $Candidate = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
    if (Test-Path $Candidate) {
        $IsccPath = $Candidate
    }
}
if ($IsccPath) {
    & $IsccPath "/DAppVersion=$Version" "packaging\windows-installer.iss"
    $Setup = Join-Path $Root "dist\SJTU-Learning-Assistant-$Version-Windows-x64-Setup.exe"
    if (-not (Test-Path $Setup)) {
        throw "Inno Setup 未生成预期安装器：$Setup"
    }
    (Get-FileHash $Setup -Algorithm SHA256).Hash.ToLowerInvariant() + "  " + (Split-Path $Setup -Leaf) |
        Set-Content "$Setup.sha256" -Encoding ascii
} else {
    Write-Warning "未找到 Inno Setup；已生成 portable ZIP，但跳过安装器。"
}

Write-Host "Windows 构建完成，产物位于 dist。"
