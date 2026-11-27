#Requires -Version 7.4
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
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
if (-not $env:SJTU_FFMPEG_STAGE) {
    $env:SJTU_FFMPEG_STAGE = Join-Path $Root "dist\ffmpeg"
}
$env:SJTU_FFMPEG_STAGE = [System.IO.Path]::GetFullPath($env:SJTU_FFMPEG_STAGE)
$env:PYTHON_BIN = $Python

# Windows 上 npm 解析为 npm.ps1 脚本垫片而非原生程序，
# $PSNativeCommandUseErrorActionPreference 不会把它的非零退出码变成错误，必须手动检查。
npm --prefix dashboard-web ci --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw "npm ci 失败（退出码 $LASTEXITCODE）。" }
npm --prefix dashboard-web run test -- --pool=forks --maxWorkers=1
if ($LASTEXITCODE -ne 0) { throw "前端测试失败（退出码 $LASTEXITCODE）。" }
npm --prefix dashboard-web run lint
if ($LASTEXITCODE -ne 0) { throw "前端 lint 失败（退出码 $LASTEXITCODE）。" }
npm --prefix dashboard-web run build
if ($LASTEXITCODE -ne 0) { throw "前端构建失败（退出码 $LASTEXITCODE）。" }
& $Python -m unittest `
    tests.test_dashboard_desktop_actions.DesktopActionSafetyTests.test_windows_commands_use_shell_free_system_handlers `
    tests.test_notifications.WindowsNotificationSenderTests `
    tests.test_ffmpeg_packaging -v
& $Python scripts/check_licenses.py
node scripts/check_licenses.mjs
& $Python scripts/scan_secrets.py

# Keep a verified reuse stage across cleanup, including the default dist/ffmpeg.
$ReuseRoot = $null
if ($env:SJTU_FFMPEG_SKIP_BUILD -eq "1" -and (Test-Path -LiteralPath $env:SJTU_FFMPEG_STAGE -PathType Container)) {
    & $Python scripts/verify_ffmpeg.py --stage $env:SJTU_FFMPEG_STAGE
    $ReuseRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("sjtu-ffmpeg-reuse-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $ReuseRoot | Out-Null
    Move-Item -LiteralPath $env:SJTU_FFMPEG_STAGE -Destination (Join-Path $ReuseRoot "ffmpeg")
}
Remove-Item build, dist -Recurse -Force -ErrorAction SilentlyContinue
if ($ReuseRoot) {
    New-Item -ItemType Directory -Force -Path (Split-Path $env:SJTU_FFMPEG_STAGE -Parent) | Out-Null
    Move-Item -LiteralPath (Join-Path $ReuseRoot "ffmpeg") -Destination $env:SJTU_FFMPEG_STAGE
    Remove-Item -LiteralPath $ReuseRoot
}
if ($env:SJTU_FFMPEG_SKIP_BUILD -eq "1") {
    & $Python scripts/verify_ffmpeg.py --stage $env:SJTU_FFMPEG_STAGE
} else {
    # setup-msys2@v2 supplies MINGW64 GCC/make; this script never installs tools.
    $MsysBash = $env:SJTU_MSYS2_BASH
    if (-not $MsysBash) {
        $MsysCandidates = @("C:\msys64\usr\bin\bash.exe")
        if ($env:RUNNER_TEMP) {
            $MsysCandidates += Join-Path $env:RUNNER_TEMP "msys64\usr\bin\bash.exe"
        }
        foreach ($Candidate in $MsysCandidates) {
            if (Test-Path -LiteralPath $Candidate) { $MsysBash = $Candidate; break }
        }
    }
    if (-not $MsysBash -or -not (Test-Path -LiteralPath $MsysBash)) {
        throw "Install/configure the MSYS2 MINGW64 toolchain and set SJTU_MSYS2_BASH to its usr\bin\bash.exe."
    }
    $env:MSYSTEM = "MINGW64"
    $env:CHERE_INVOKING = "1"
    $env:SJTU_BUILD_ROOT = $Root
    & $MsysBash -lc 'cd "$(cygpath -u "$SJTU_BUILD_ROOT")" && bash scripts/build_ffmpeg.sh'
}
& $Python -m PyInstaller --noconfirm --clean packaging/windows.spec

$Version = (& $Python -c "from sjtu_learning_assistant import __version__; print(__version__)").Trim()
$AppDir = Join-Path $Root "dist\SJTU Learning Assistant"
$Executable = Join-Path $AppDir "SJTU Learning Assistant.exe"
if (-not (Test-Path $Executable)) {
    throw "PyInstaller 未生成预期可执行文件：$Executable"
}
& $Python scripts/verify_windows_manifest.py $Executable
if ($LASTEXITCODE -ne 0) { throw "Windows executable manifest validation failed." }
$FrontendIndex = Join-Path $AppDir "_internal\dashboard-web\dist\index.html"
& $Python -c "import sys; from pathlib import Path; from sjtu_learning_assistant.frontend_startup import validate_frontend; validate_frontend(Path(sys.argv[1]))" $FrontendIndex
if ($LASTEXITCODE -ne 0) { throw "Windows bundle frontend resource validation failed." }
$SeedGlossaryDir = Join-Path $AppDir "_internal\resources\seed_glossary"
$ExpectedSeedGlossaries = @(
    "traditional_machine_learning.json",
    "artificial_intelligence.json",
    "regression_analysis.json",
    "finance_economics.json",
    "accounting.json",
    "marketing.json"
)
foreach ($SeedGlossary in $ExpectedSeedGlossaries) {
    $SeedGlossaryPath = Join-Path $SeedGlossaryDir $SeedGlossary
    if (-not (Test-Path $SeedGlossaryPath -PathType Leaf)) {
        throw "Windows bundle 缺少种子词表：$SeedGlossary"
    }
}

$BundledFfmpeg = Join-Path $AppDir "_internal\ffmpeg\bin\ffmpeg.exe"
if (-not (Test-Path -LiteralPath $BundledFfmpeg -PathType Leaf)) {
    throw "Windows bundle 缺少专用 FFmpeg：$BundledFfmpeg"
}
& $Python scripts/verify_ffmpeg.py --bundle $AppDir

$Zip = Join-Path $Root "dist\SJTU-Learning-Assistant-$Version-Windows-x64-portable.zip"
Get-ChildItem -LiteralPath $AppDir -Force | Compress-Archive -DestinationPath $Zip -CompressionLevel Optimal -Force
$ZipHash = (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText(
    "$Zip.sha256",
    "$ZipHash  $(Split-Path $Zip -Leaf)`n",
    [System.Text.Encoding]::ASCII
)

$IsccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
$IsccPath = if ($IsccCommand) { $IsccCommand.Source } else { $null }
if (-not $IsccPath) {
    foreach ($Base in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, (Join-Path $env:LOCALAPPDATA "Programs"))) {
        if (-not $Base) { continue }
        $Candidate = Join-Path $Base "Inno Setup 6\ISCC.exe"
        if (Test-Path -LiteralPath $Candidate) {
            $IsccPath = $Candidate
            break
        }
    }
}
if ($IsccPath) {
    & $IsccPath "/DAppVersion=$Version" "packaging\windows-installer.iss"
    $Setup = Join-Path $Root "dist\SJTU-Learning-Assistant-$Version-Windows-x64-Setup.exe"
    if (-not (Test-Path $Setup)) {
        throw "Inno Setup 未生成预期安装器：$Setup"
    }
    $SetupHash = (Get-FileHash $Setup -Algorithm SHA256).Hash.ToLowerInvariant()
    [System.IO.File]::WriteAllText(
        "$Setup.sha256",
        "$SetupHash  $(Split-Path $Setup -Leaf)`n",
        [System.Text.Encoding]::ASCII
    )
} else {
    if ($env:GITHUB_ACTIONS -eq "true") {
        throw "CI 中未找到 Inno Setup，拒绝在缺少安装器的情况下继续发布。"
    }
    Write-Warning "未找到 Inno Setup；已生成 portable ZIP，但跳过安装器。"
}

Write-Host "Windows 构建完成，产物位于 dist。"
