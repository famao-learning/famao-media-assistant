#define AppName "自媒体发布助手"
#ifndef AppVersion
#define AppVersion "1.3.0"
#endif
#define AppExe "MediaAssistant.exe"
#ifndef AppIdentity
#define AppIdentity "{{A51ACF70-941E-426C-BC8C-9E1D68608BA7}"
#endif
#ifndef PackageName
#define PackageName "MediaAssistant-Setup-" + AppVersion
#endif
[Setup]
AppId={#AppIdentity}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=自媒体发布助手
DefaultDirName={localappdata}\Programs\MediaAssistant
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\release
OutputBaseFilename={#PackageName}
SetupIconFile=..\static\brand\app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
InfoBeforeFile=使用说明.txt

[Languages]
Name: "zhcn"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "..\dist\v{#AppVersion}\MediaAssistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "打开{#AppName}"; Flags: nowait postinstall skipifsilent

; No UninstallDelete for profiles/data: user data intentionally survives upgrades/uninstall.
