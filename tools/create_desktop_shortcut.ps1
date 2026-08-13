param(
    [Parameter(Mandatory = $true)]
    [string]$ExePath,
    [string]$ShortcutPath = ""
)

$ErrorActionPreference = "Stop"
$appName = -join ([char[]](0x804A, 0x5C0F, 0x667A))
$expectedExeName = "$appName.exe"
$versionPattern = '^' + [regex]::Escape($appName) + '_\d{4}\.\d{2}\.\d{2}\.\d+$'
$resolvedExe = (Resolve-Path -LiteralPath $ExePath).Path
$exe = Get-Item -LiteralPath $resolvedExe
if ($exe.Name -ne $expectedExeName) {
    throw "The target must be the versioned release executable."
}
$packageDir = $exe.Directory
$versionDir = $packageDir.Parent
if ($packageDir.Name -ne $appName -or $versionDir.Name -notmatch $versionPattern) {
    throw "The executable must be inside APP_YYYY.MM.DD.N\APP."
}
if ([string]::IsNullOrWhiteSpace($ShortcutPath)) {
    $shellForDesktop = New-Object -ComObject WScript.Shell
    $ShortcutPath = Join-Path $shellForDesktop.SpecialFolders.Item("Desktop") "$appName.lnk"
}
$shortcutParent = Split-Path -Parent $ShortcutPath
if (-not (Test-Path -LiteralPath $shortcutParent)) {
    throw "The shortcut directory does not exist: $shortcutParent"
}
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $resolvedExe
$shortcut.WorkingDirectory = $packageDir.FullName
$shortcut.IconLocation = "$resolvedExe,0"
$shortcut.Description = "$appName release $($versionDir.Name.Substring($appName.Length + 1))"
$shortcut.Save()

$saved = $shell.CreateShortcut((Resolve-Path -LiteralPath $ShortcutPath).Path)
if ($saved.TargetPath -ne $resolvedExe -or $saved.WorkingDirectory -ne $packageDir.FullName) {
    throw "Shortcut verification failed."
}
Write-Output "SHORTCUT_OK: $ShortcutPath -> $resolvedExe"
