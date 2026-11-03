param([string]$ArcGISProInstallDir = $env:ARCGIS_PRO_HOME, [switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
if ($ArcGISProInstallDir) { $env:ARCGIS_PRO_HOME = $ArcGISProInstallDir }
if (-not $SkipBuild) {
  & py -3.12 (Join-Path $PSScriptRoot 'build_roslyn.py')
  if ($LASTEXITCODE -ne 0) { throw '统一插件编译失败。' }
}
& py -3.12 (Join-Path $PSScriptRoot 'package.py')
if ($LASTEXITCODE -ne 0) { throw '统一插件打包失败。' }
