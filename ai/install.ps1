param([string]$PackagePath = '', [string]$ArcGISProHome = $env:ARCGIS_PRO_HOME)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot '../addin/install_addin.ps1') -PackagePath $PackagePath -ArcGISProInstallDir $ArcGISProHome
if (-not $?) { throw '统一插件安装失败。' }
