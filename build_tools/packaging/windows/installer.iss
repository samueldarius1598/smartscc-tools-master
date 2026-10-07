#define AppId "{{9A274E4C-0D70-42C4-99DF-3C4F4E1C9B47}"
#define AppName "Smart's CC Tools Master"
#define AppPublisher "Smart's CC"
#define AppExeName "smartscc_tools_master.exe"
#define OutputBaseName "SmartsCC-ToolsMaster-Setup"

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

#ifndef SourceDir
  #error SourceDir preprocessor variable is required.
#endif

#ifndef OutputDir
  #define OutputDir "build\release\installer"
#endif

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter={#AppExeName}
RestartApplications=no
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseName}-{#AppVersion}
UninstallDisplayIcon={app}\_internal\icon\mainlogo.ico
SetupIconFile={#SourceDir}\_internal\icon\mainlogo.ico
SetupLogging=yes

[Languages]
Name: "indonesian"; MessagesFile: "compiler:Languages\Indonesian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\_internal\icon\mainlogo.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\_internal\icon\mainlogo.ico"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Jalankan {#AppName}"; Flags: nowait postinstall skipifsilent
