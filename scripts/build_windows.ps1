param([string]$Iscc = "$PSScriptRoot\..\tools\packaging\InnoSetup\ISCC.exe",
      [string]$Python = "$PSScriptRoot\..\.venv\Scripts\python.exe")
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $projectRoot
try {
    $version = (& $Python -c "from core.app_paths import VERSION; print(VERSION)").Trim()
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+\.\d+\.\d+$') { throw '无法读取应用版本' }
    $bundleRoot = Join-Path $projectRoot "dist\v$version"
    & $Python scripts\collect_runtime_licenses.py
    if ($LASTEXITCODE -ne 0) { throw '依赖声明收集失败' }
    & $Python -m PyInstaller --noconfirm --clean --distpath $bundleRoot packaging\MediaAssistant.spec
    if ($LASTEXITCODE -ne 0) { throw 'EXE 打包失败' }
    & $Python scripts\verify_matrix_packaged.py (Join-Path $bundleRoot 'MediaAssistant\MediaAssistant.exe')
    if ($LASTEXITCODE -ne 0) { throw '安装版运行验证失败，停止生成安装包' }
    if (!(Test-Path -LiteralPath $Iscc)) { throw '请安装 Inno Setup，并通过 -Iscc 指定 ISCC.exe' }
    & $Iscc "/DAppVersion=$version" packaging\setup.iss
    if ($LASTEXITCODE -ne 0) { throw '安装包编译失败' }
    $installer = "release\MediaAssistant-Setup-$version.exe"
    $hash = Get-FileHash $installer -Algorithm SHA256
    ($hash.Hash.ToLower() + "  MediaAssistant-Setup-$version.exe") | Set-Content "release\SHA256SUMS-$version.txt" -Encoding utf8
    Copy-Item packaging\使用说明.txt release\安装说明.txt -Force
    Copy-Item data\package-check-result.json "release\运行验证-$version.json" -Force
    $hash | Format-List
} finally { Pop-Location }
