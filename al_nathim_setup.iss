; Inno Setup Script for الناظم - لإدارة أبراج الإنترنت
; Open this file in Inno Setup Compiler and click "Compile" to build the installer

[Setup]
AppName=الناظم لإدارة أبراج الإنترنت
AppVersion=1.1
AppVerName=الناظم 1.1
AppPublisher=Al-Nathim ISP Systems
AppPublisherURL=https://alnathim.com
AppSupportURL=https://alnathim.com
DefaultDirName={autopf}\Al-Nathim
DefaultGroupName=الناظم
UninstallDisplayIcon={app}\Al-Nathim.exe
UninstallDisplayName=الناظم لإدارة أبراج الإنترنت
Compression=lzma2
SolidCompression=yes
OutputDir=installer
OutputBaseFilename=Al-Nathim-Setup-1.1
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
DisableDirPage=no
DisableProgramGroupPage=no
WizardStyle=modern
SetupLogging=yes
VersionInfoVersion=1.1.0.0
VersionInfoCompany=Al-Nathim ISP Systems
VersionInfoDescription=نظام إدارة أبراج الإنترنت الناظم
VersionInfoOriginalFileName=Al-Nathim-Setup-1.1.exe

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"

[Tasks]
Name: "desktopicon"; Description: "إنشاء اختصار على سطح المكتب"; GroupDescription: "المهام الإضافية:"; Flags: checkablealone
Name: "quicklaunchicon"; Description: "إنشاء اختصار في شريط التشغيل السريع"; GroupDescription: "المهام الإضافية:"; Flags: checkablealone; OnlyBelowVersion: 5.4

[Files]
Source: "dist\Al-Nathim.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "al-nazim-icon.svg"; DestDir: "{app}"; Flags: ignoreversion
Source: ".env"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

[Icons]
Name: "{commondesktop}\الناظم"; Filename: "{app}\Al-Nathim.exe"; Tasks: desktopicon; Comment: "الناظم - لإدارة أبراج الإنترنت"; WorkingDir: "{app}"
Name: "{group}\الناظم"; Filename: "{app}\Al-Nathim.exe"; Comment: "الناظم - لإدارة أبراج الإنترنت"; WorkingDir: "{app}"
Name: "{group}\إلغاء تثبيت الناظم"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\Al-Nathim.exe"; Description: "تشغيل الناظم الآن"; Flags: postinstall nowait skipifsilent shellexec

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c taskkill /f /im Al-Nathim.exe >nul 2>&1"; Flags: runhidden