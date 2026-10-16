#define AppName "SJTU Learning Assistant"
#ifndef AppVersion
  #define AppVersion "1.7.0"
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
MinVersion=10.0

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#RootDir}\dist\SJTU Learning Assistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\SJTU Learning Assistant.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\SJTU Learning Assistant.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标："

[Run]
Filename: "{app}\SJTU Learning Assistant.exe"; Description: "启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
const
  WebView2ClientKey = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2DownloadUrl = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';

function WebView2VersionValid(const Version: String): Boolean;
begin
  Result := (Version <> '') and (Version <> '0.0.0.0');
end;

function IsWebView2Installed: Boolean;
var
  Version: String;
begin
  Result := False;
  if RegQueryStringValue(HKLM32, WebView2ClientKey, 'pv', Version) and WebView2VersionValid(Version) then
    Result := True
  else if RegQueryStringValue(HKCU, WebView2ClientKey, 'pv', Version) and WebView2VersionValid(Version) then
    Result := True;
end;

function InitializeSetup: Boolean;
var
  ErrorCode: Integer;
begin
  Result := True;
  if IsWebView2Installed or WizardSilent then
    exit;
  if MsgBox('未检测到 Microsoft Edge WebView2 运行时，SJTU 学习助手需要它显示界面。' + #13#10 + #13#10 + '是否现在打开微软官方下载页？安装 WebView2 后即可正常启动本应用。', mbConfirmation, MB_YESNO) = IDYES then
    ShellExec('open', WebView2DownloadUrl, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;
