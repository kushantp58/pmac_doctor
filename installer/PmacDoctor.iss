; Inno Setup script for PMAC Doctor.
; Compiled by build\build.ps1 (ISCC.exe). Expects the PyInstaller output in dist\PmacDoctor.

#define MyAppName "PMAC Doctor"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "NSLS-II, Brookhaven National Laboratory"
#define MyAppExeName "PmacDoctor.exe"
#ifndef SourceDir
  #define SourceDir "..\dist\PmacDoctor"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\installer"
#endif

[Setup]
AppId={{8C1B6B7E-4E4B-4F1A-9B3B-PMACDOCTOR01}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\PmacDoctor
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; allow a per-user install without admin rights (falls back automatically)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#OutputDir}
OutputBaseFilename=PmacDoctor-{#MyAppVersion}-setup
SetupIconFile=..\pmacdoctor\ui\resources\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\USER_GUIDE.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\User guide"; Filename: "{app}\docs\USER_GUIDE.md"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
