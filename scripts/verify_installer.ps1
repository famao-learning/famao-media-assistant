param([string]$Version = '1.1.8', [string]$Python = "$PSScriptRoot\..\.venv\Scripts\python.exe",
      [string]$Iscc = "$PSScriptRoot\..\tools\packaging\InnoSetup\ISCC.exe")
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
# A separate installer identity protects an existing production installation.
$identity = [guid]::NewGuid().ToString().ToUpper()
$checkName = "MediaAssistant-Check-$Version"
& $Iscc "/DAppVersion=$Version" ("/DAppIdentity={{" + $identity + "}") "/DPackageName=$checkName" (Join-Path $projectRoot 'packaging\setup.iss')
if ($LASTEXITCODE -ne 0) { throw '隔离验证安装包编译失败' }
$checkRoot = Join-Path $projectRoot ('data\install-verification-' + [guid]::NewGuid().ToString('N'))
$installDir = Join-Path $checkRoot 'program'
New-Item -ItemType Directory -Path $checkRoot | Out-Null
$sentinel = Join-Path $checkRoot 'retained-user-data.txt'
Set-Content -LiteralPath $sentinel -Value 'user data must survive uninstall'
$setup = Join-Path $projectRoot "release\$checkName.exe"
$log = Join-Path $checkRoot 'install.log'
$result = Start-Process -FilePath $setup -WindowStyle Hidden -Wait -PassThru -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOICONS','/TASKS=""',('/DIR="' + $installDir + '"'),('/LOG="' + $log + '"'))
if ($result.ExitCode -ne 0) { throw "安装失败：$($result.ExitCode)" }
try {
    & $Python (Join-Path $PSScriptRoot 'verify_packaged.py') (Join-Path $installDir 'MediaAssistant.exe')
    if ($LASTEXITCODE -ne 0) { throw '安装后的 EXE 验证失败' }
    # Reinstall to the same directory exercises the upgrade path.
    $again = Start-Process -FilePath $setup -WindowStyle Hidden -Wait -PassThru -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOICONS','/TASKS=""',('/DIR="' + $installDir + '"'))
    if ($again.ExitCode -ne 0) { throw '覆盖安装失败' }
} finally {
    $uninstaller = (Resolve-Path (Join-Path $installDir 'unins000.exe')).Path
    if ((Split-Path $uninstaller -Parent) -ne $installDir -or !$installDir.StartsWith($projectRoot + '\data\install-verification-')) { throw '卸载测试路径校验失败' }
    $removed = Start-Process -FilePath $uninstaller -WindowStyle Hidden -Wait -PassThru -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART')
    if ($removed.ExitCode -ne 0) { throw '卸载验证失败' }
}
if (!(Test-Path -LiteralPath $sentinel) -or (Test-Path -LiteralPath (Join-Path $installDir 'MediaAssistant.exe'))) { throw '卸载后的数据检查失败' }
@{ok=$true;version=$Version;isolated_identity=$identity;install=$true;upgrade=$true;uninstall=$true;data_retained=$true} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot "release\安装验证-$Version.json") -Encoding utf8
Write-Output 'PASS: installation, installed EXE, upgrade, uninstall, retained data'
