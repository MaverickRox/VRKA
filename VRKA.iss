; VRKA 3.5 build 012 - Windows x64 installer (Inno Setup 6)
; Keeps the original Seal Desktop AppId so installs upgrade in place.

#define MyAppName "VRKA"
#define MyAppVersion "3.5"
#define MyAppPublisher "MVRK"
#define MyAppExeName "VRKA.exe"

[Setup]
AppId={{7C6E2F1A-4B3D-4E9A-9F2C-1A8D5E6B3C90}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion=3.5.0.12
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
DisableProgramGroupPage=yes
Compression=lzma2
SolidCompression=yes
OutputDir=installer_output
OutputBaseFilename=VRKA-3.5.0-setup-Windows-x64
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
; Remove the obsolete executable name when upgrading an existing Seal Desktop install.
Type: files; Name: "{app}\SealDesktop.exe"
Type: files; Name: "{autodesktop}\Seal Desktop.lnk"
Type: files; Name: "{autoprograms}\Seal Desktop\Seal Desktop.lnk"
Type: files; Name: "{autoprograms}\Seal Desktop\Uninstall Seal Desktop.lnk"
Type: files; Name: "{autoprograms}\Seal Desktop\VRKA.lnk"
Type: files; Name: "{autoprograms}\Seal Desktop\Uninstall VRKA.lnk"
Type: dirifempty; Name: "{autoprograms}\Seal Desktop"

[Files]
Source: "dist\VRKA.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Use an explicit VRKA group so legacy installs cannot retain a Seal-branded Start Menu folder.
Name: "{autoprograms}\{#MyAppName}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\{#MyAppName}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
