#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{8D4F6E2A-9B3C-4E12-A5F1-3C8E7D2B1A09}
AppName=Colab Canvas Studio
AppVersion={#MyAppVersion}
AppPublisher=alan0x
AppPublisherURL=https://github.com/alan0x/colab-media-studio
DefaultDirName={localappdata}\Programs\Colab Canvas Studio
DefaultGroupName=Colab Canvas Studio
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Colab-Canvas-Studio-v{#MyAppVersion}-Windows-x64-Setup
SetupIconFile=AppIcon.ico
UninstallDisplayIcon={app}\Colab Canvas Studio.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut (创建桌面快捷方式)"; GroupDescription: "Additional icons:"; Flags: checkedonce

[Files]
Source: "..\dist\Colab Canvas Studio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Colab Canvas Studio"; Filename: "{app}\Colab Canvas Studio.exe"; IconFilename: "{app}\Colab Canvas Studio.exe"
Name: "{group}\Uninstall Colab Canvas Studio"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Colab Canvas Studio"; Filename: "{app}\Colab Canvas Studio.exe"; IconFilename: "{app}\Colab Canvas Studio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Colab Canvas Studio.exe"; Description: "Launch Colab Canvas Studio (立即启动)"; Flags: nowait postinstall skipifsilent
