# 构建环境

工程面向 ArcGIS Pro 3.1.x / SDK 3.1 / .NET 6。标准构建需要 .NET 6 SDK 和 ArcGIS Pro 安装目录中的 SDK assemblies。`package.ps1` 编译 Add-In DLL，再按 Pro Add-In ZIP 结构打入 DAML、Python toolbox 和核心模块；安装由单独的 `install_addin.ps1` 执行。

```powershell
.\package.ps1 -ArcGISProInstallDir 'D:\ArcGIS\Pro' -DotnetRoot 'C:\Program Files\dotnet'
```

本机没有 .NET SDK 时，可显式选择 Roslyn 回退构建：

```powershell
.\package.ps1 -ArcGISProInstallDir 'D:\ArcGIS\Pro' -UseRoslynFallback
```

回退构建要求 Visual Studio Roslyn `csc.exe`、.NET 6 与 Windows Desktop 6 运行时、ArcGIS Pro 3.1 SDK assemblies。它从已安装运行时加载编译引用程序集，不使用 .NET SDK reference packs 或 Esri MSBuild targets；当前已在本机 Roslyn 4.14 / Pro 3.1.5 构建成功，仍需在启动后的 Pro 会话确认 Add-In 加载和按钮交互。若目标环境无法满足这些条件，应安装正式 .NET 6 SDK 并使用标准构建。

ArcGIS Pro 安装目录可由 `ARCGIS_PRO_HOME` 或 Pro 注册表读取。路径只作为构建参数传入，不写入源代码或安装包。当前宿主的 Visual Studio Build Tools 缺少 .NET SDK resolver，因此不运行 Pro targets 中的 `CodeTaskFactory` 打包目标；手工打包后仍需由 Pro 的 `RegisterAddIn.exe` 和 Pro 界面验证结构及加载情况。

打包脚本检查 DAML 控件引用、命令类和 KeyTip，并递归核对所有 Python 核心模块已打包且 SHA-256 与源码一致。成功读回只是包结构检查，不代表 Pro UI 已成功加载。
