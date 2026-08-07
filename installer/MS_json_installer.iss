; MS JSON 安装包脚本（Inno Setup 6.3+）
; 构建命令见 ../build_installer.bat；版本号可由 ISCC /DMyAppVersion=1.2.3 覆盖。

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#define MyAppName "MS JSON"
#define MyAppExeName "MS_json.exe"
#define MyAppPublisher "MS JSON"

[Setup]
AppId={{20B3629F-AA54-482B-9814-285DAA71BC78}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=..\dist
OutputBaseFilename=MS_json_Setup
SetupIconFile=..\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 卸载后保留用户在工作目录中生成的导出文件（卸载仅删除安装目录）

[Languages]
; 简体中文语言文件随项目分发（languages\ChineseSimplified.isl，来自
; github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation），
; 不依赖 Inno Setup 安装目录，便于任何机器编译。
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; 将 onedir 构建产物整个目录解压到安装目录
Source: "..\dist\MS_json\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
