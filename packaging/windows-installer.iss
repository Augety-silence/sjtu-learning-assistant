#define AppName "SJTU Learning Assistant"
#ifndef AppVersion
  #define AppVersion "1.2.0"
#endif
#define RootDir SourcePath + "\.."

[Setup]
AppId={{A167694E-E57C-4B43-9272-D97F94658C1D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=SJTU Learning Assistant
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#RootDir}\dist
OutputBaseFilename=SJTU-Learning-Assistant-{#AppVersion}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
SetupIconFile={#RootDir}\packaging\app.ico
UninstallDisplayIcon={app}\SJTU Learning Assistant.exe
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Files]
Source: "{#RootDir}\dist\SJTU Learning Assistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\SJTU Learning Assistant.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\SJTU Learning Assistant.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标："

[Run]
Filename: "{app}\SJTU Learning Assistant.exe"; Description: "启动 {#AppName}"; Flags: nowait postinstall skipifsilent
