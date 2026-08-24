#define AppName "聊小智"
#ifndef AppVersion
  #define AppVersion "2026.08.24.2"
#endif
#ifndef AppVersionNumeric
  #define AppVersionNumeric "2026.8.24.2"
#endif
#ifndef PackageSource
  #define PackageSource "..\dist\聊小智"
#endif
#ifndef InstallerOutputDir
  #define InstallerOutputDir "..\dist\release"
#endif
#ifndef InstallerAppId
  #define InstallerAppId "{{D67B9715-251B-4BBE-B2AB-3481CC740C70}"
#endif
#define AppPublisher "聊小智"
#define AppExeName "聊小智.exe"

[Setup]
AppId={#InstallerAppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#InstallerOutputDir}
OutputBaseFilename=聊小智_完整安装包_{#AppVersion}
SetupIconFile=..\assets\app_icon_clean.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter={#AppExeName}
RestartApplications=no
UninstallDisplayIcon={app}\{#AppExeName}
VersionInfoVersion={#AppVersionNumeric}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoDescription={#AppName} 完整安装包
VersionInfoCompany={#AppPublisher}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Dirs]
Name: "{app}\user_data"; Permissions: users-modify; Flags: uninsneveruninstall

[Files]
; 程序文件始终安装；user_data 由下方发布种子单独控制，绝不覆盖既有用户数据。
Source: "{#PackageSource}\*"; DestDir: "{app}"; Excludes: "user_data\*"; Flags: ignoreversion recursesubdirs createallsubdirs
; 全新安装写入无密钥设置与完整知识库；重复安装时保留用户已有设置、知识和向量。
Source: "{#PackageSource}\user_data\settings.json"; DestDir: "{app}\user_data"; Flags: ignoreversion uninsneveruninstall; Check: ShouldInstallSeedSettings
Source: "{#PackageSource}\user_data\memory.json"; DestDir: "{app}\user_data"; Flags: ignoreversion uninsneveruninstall; Check: ShouldInstallSeedKnowledge
Source: "{#PackageSource}\user_data\vector_memory.json"; DestDir: "{app}\user_data"; Flags: ignoreversion uninsneveruninstall; Check: ShouldInstallSeedKnowledge

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  SeedKnowledgeDecisionSet: Boolean;
  InstallSeedKnowledge: Boolean;

function ShouldInstallSeedKnowledge(): Boolean;
begin
  if not SeedKnowledgeDecisionSet then
  begin
    InstallSeedKnowledge := not FileExists(ExpandConstant('{app}\user_data\memory.json'));
    SeedKnowledgeDecisionSet := True;
  end;
  Result := InstallSeedKnowledge;
end;

function ShouldInstallSeedSettings(): Boolean;
begin
  Result := not FileExists(ExpandConstant('{app}\user_data\settings.json'));
end;
