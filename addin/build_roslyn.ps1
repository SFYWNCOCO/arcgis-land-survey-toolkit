param([string]$ArcGISProInstallDir = $env:ARCGIS_PRO_HOME, [string]$CompilerPath)
$ErrorActionPreference = 'Stop'
if ($ArcGISProInstallDir) { $env:ARCGIS_PRO_HOME = $ArcGISProInstallDir }
if ($CompilerPath) { $env:CSC_PATH = $CompilerPath }
& py -3.12 (Join-Path $PSScriptRoot 'build_roslyn.py')
if ($LASTEXITCODE -ne 0) { throw '统一插件编译失败。' }
