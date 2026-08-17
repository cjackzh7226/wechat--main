#define AppName "聊小智"
#define AppVersion "2026.08.18.1"
#define AppPublisher "聊小智"
#define AppExeName "聊小智.exe"

[Setup]
; 与首次安装包保持相同 AppId 和默认目录，确保覆盖升级到既有安装位置。
AppId={{D67B9715-251B-4BBE-B2AB-3481CC740C70}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\release
OutputBaseFilename=聊小智_更新包_{#AppVersion}
SetupIconFile=..\assets\app_icon_clean.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter={#AppExeName}
RestartApplications=no
UninstallDisplayIcon={app}\{#AppExeName}
VersionInfoVersion=2026.8.18.1
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoDescription={#AppName} 覆盖升级包（保留用户数据）
VersionInfoCompany={#AppPublisher}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Dirs]
; 升级和以后卸载都不删除用户数据目录。
Name: "{app}\user_data"; Flags: uninsneveruninstall

[Files]
; 只覆盖程序文件；知识库、向量库、设置、API Key、发送台账均位于 user_data，全部排除。
Source: "..\dist\聊小智\*"; DestDir: "{app}"; Excludes: "user_data\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动 {#AppName}"; Flags: nowait postinstall skipifsilent
