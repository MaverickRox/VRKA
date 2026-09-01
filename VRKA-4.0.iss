; VRKA 4.0.0 build 016 - Windows x64 installer (Inno Setup 6)
#define MyAppName "VRKA"
#define MyAppVersion "4.0.0"
#define MyAppPublisher "VRKA"
#define MyAppExeName "VRKA.exe"

[Setup]
AppId={{7C6E2F1A-4B3D-4E9A-9F2C-1A8D5E6B3C90}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion=4.0.0.16
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
DisableProgramGroupPage=yes
Compression=lzma2/ultra64
SolidCompression=yes
OutputDir=outputs\VRKA-4.0.0-build016-release
OutputBaseFilename=VRKA-4.0.0-build016-setup-Windows-x64
SetupIconFile=assets\branding\vrka.ico
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[InstallDelete]
Type: files; Name: "{app}\SealDesktop.exe"
Type: files; Name: "{autodesktop}\Seal Desktop.lnk"
Type: files; Name: "{autoprograms}\Seal Desktop\Seal Desktop.lnk"

[Files]
Source: "VRKA-portable\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "VRKA-portable\ffmpeg_bin\*"; DestDir: "{app}\ffmpeg_bin"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
