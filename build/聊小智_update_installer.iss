#define AppName "聊小智"
#ifndef AppVersion
  #define AppVersion "2026.09.12.3"
#endif
#ifndef AppVersionNumeric
  #define AppVersionNumeric "2026.9.12.3"
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
; 与首次安装包保持相同 AppId 和默认目录，确保覆盖升级到既有安装位置。
AppId={#InstallerAppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
AppendDefaultDirName=no
Uninstallable=no
CreateUninstallRegKey=no
PrivilegesRequired=lowest
OutputDir={#InstallerOutputDir}
OutputBaseFilename=聊小智_更新包_{#AppVersion}
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
; 明确白名单：更新包只能写程序 EXE 和运行库，不能复制根目录配置、种子数据或快捷方式。
Source: "{#PackageSource}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PackageSource}\_internal\*"; DestDir: "{app}\_internal"; Excludes: "user_data\*,settings.json,memory.json,vector_memory.json,credentials.dat,send_ledger.json"; Flags: ignoreversion recursesubdirs createallsubdirs

[Code]
var
  DataRoot, BackupRoot: String;
  DataFiles, DataHashes: TArrayOfString;
  DataVerified: Boolean;

function ValidateUpdateTarget: String;
begin
  Result := '';
  if not FileExists(ExpandConstant('{app}\{#AppExeName}')) then
    Result := '请选择原来使用的聊小智.exe 所在目录。此更新包不能安装到新目录。'
  else if not FileExists(ExpandConstant('{app}\user_data\settings.json')) then
    Result := '所选目录没有原设置文件，已停止更新。请重新选择原程序目录。'
  else if not FileExists(ExpandConstant('{app}\user_data\memory.json')) then
    Result := '所选目录没有原话术库文件，已停止更新。请重新选择保存原话术库的程序目录。';
end;

procedure InitializeWizard;
begin
  WizardForm.SelectDirLabel.Caption :=
    '选择原来使用的聊小智.exe 所在目录。更新只替换程序，原 API 配置和话术库保持不变。';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := ValidateUpdateTarget;
end;

procedure SnapshotDirectory(const RelativeDir: String);
var
  Found: TFindRec;
  RelativePath, SourcePath, TargetPath, Digest: String;
  Count: Integer;
begin
  if not ForceDirectories(AddBackslash(BackupRoot) + RelativeDir) then
    RaiseException('无法建立数据备份目录。');
  if not FindFirst(AddBackslash(DataRoot) + RelativeDir + '*', Found) then
    RaiseException('无法完整读取原数据目录。');
  try
    repeat
      if (Found.Name <> '.') and (Found.Name <> '..') then begin
        if (Found.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0 then
          RaiseException('原数据包含目录链接，无法保证完整备份，已停止更新。');
        RelativePath := RelativeDir + Found.Name;
        if (Found.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
          SnapshotDirectory(AddBackslash(RelativePath))
        else begin
          SourcePath := AddBackslash(DataRoot) + RelativePath;
          TargetPath := AddBackslash(BackupRoot) + RelativePath;
          Digest := GetSHA256OfFile(SourcePath);
          if not FileCopy(SourcePath, TargetPath, True) then
            RaiseException('无法备份原数据文件。');
          if GetSHA256OfFile(TargetPath) <> Digest then
            RaiseException('原数据备份校验失败。');
          Count := GetArrayLength(DataFiles);
          SetArrayLength(DataFiles, Count + 1);
          SetArrayLength(DataHashes, Count + 1);
          DataFiles[Count] := RelativePath;
          DataHashes[Count] := Digest;
        end;
      end;
    until not FindNext(Found);
  finally
    FindClose(Found);
  end;
end;

procedure BackupOriginalData;
var
  BasePath, Candidate: String;
  Suffix: Integer;
  Found: TFindRec;
begin
  DataRoot := ExpandConstant('{app}\user_data');
  if not FindFirst(DataRoot, Found) then
    RaiseException('无法读取原数据目录。');
  try
    if (Found.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0 then
      RaiseException('原数据目录是链接，已停止更新。');
  finally
    FindClose(Found);
  end;
  BasePath := ExpandConstant('{app}\update_backups\{#AppVersion}-') +
    GetDateTimeString('yyyymmdd-hhnnss', '-', ':');
  Candidate := BasePath;
  Suffix := 0;
  while DirExists(Candidate) or FileExists(Candidate) do begin
    Suffix := Suffix + 1;
    Candidate := BasePath + '-' + IntToStr(Suffix);
  end;
  BackupRoot := AddBackslash(Candidate) + 'user_data';
  SetArrayLength(DataFiles, 0);
  SetArrayLength(DataHashes, 0);
  SnapshotDirectory('');
  if GetArrayLength(DataFiles) = 0 then
    RaiseException('没有找到可备份的原数据，已停止更新。');
  Log('Original user data backed up and verified. Files: ' + IntToStr(GetArrayLength(DataFiles)));
end;

procedure VerifyOriginalData;
var
  Index: Integer;
  FilePath: String;
begin
  for Index := 0 to GetArrayLength(DataFiles) - 1 do begin
    FilePath := AddBackslash(DataRoot) + DataFiles[Index];
    if not FileExists(FilePath) then
      RaiseException('更新后原数据文件缺失。');
    if GetSHA256OfFile(FilePath) <> DataHashes[Index] then
      RaiseException('更新后原数据校验不一致。');
  end;
  DataVerified := True;
  Log('Original user data unchanged: SHA256 verified for every file.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Problem: String;
begin
  if CurStep = ssInstall then begin
    DataVerified := False;
    Problem := ValidateUpdateTarget;
    if Problem = '' then begin
      try
        { 此时安装器已处理正在运行的程序，且尚未复制任何程序文件。 }
        BackupOriginalData;
      except
        Problem := GetExceptionMessage;
      end;
    end;
    if Problem <> '' then begin
      Log('Update stopped before program replacement: ' + Problem);
      SuppressibleMsgBox(Problem + #13#10 + '尚未替换程序文件，原数据保持不变。', mbError, MB_OK, IDOK);
      Abort;
    end;
  end;
  if CurStep = ssPostInstall then begin
    try
      VerifyOriginalData;
    except
      Problem := GetExceptionMessage;
      Log('User data verification failed: ' + Problem);
      SuppressibleMsgBox(Problem + #13#10 + '原数据备份位于：' + BackupRoot + #13#10 +
        '请保留该备份并停止使用程序。', mbError, MB_OK, IDOK);
    end;
  end;
end;

function GetCustomSetupExitCode: Integer;
begin
  if DataVerified then Result := 0 else Result := 42;
end;
