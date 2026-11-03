param([string]$PackagePath = '', [string]$ArcGISProInstallDir = $env:ARCGIS_PRO_HOME)
$ErrorActionPreference = 'Stop'
if (Get-Process -Name ArcGISPro -ErrorAction SilentlyContinue) { throw '请先保存并关闭 ArcGIS Pro，再安装统一插件。' }
if (-not $PackagePath) {
  $receipt = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'dist/build-receipt.json') -Raw -Encoding UTF8 | ConvertFrom-Json
  $PackagePath = Join-Path (Join-Path $PSScriptRoot 'dist') ([IO.Path]::GetFileName($receipt.path))
}
if (-not $ArcGISProInstallDir) {
  foreach ($key in @('HKLM:\SOFTWARE\ESRI\ArcGISPro', 'HKCU:\SOFTWARE\ESRI\ArcGISPro')) {
    if (Test-Path $key) { $ArcGISProInstallDir = (Get-ItemProperty $key).InstallDir; if ($ArcGISProInstallDir) { break } }
  }
}
$register = Join-Path $ArcGISProInstallDir 'bin\RegisterAddIn.exe'
if (-not (Test-Path $register)) { throw '找不到 ArcGIS Pro RegisterAddIn.exe；请指定正确的 -ArcGISProInstallDir。' }
if (-not (Test-Path $PackagePath)) { throw "找不到 Add-In 包：$PackagePath" }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path $PackagePath))
try {
  $entry = $archive.GetEntry('Config.daml')
  if (-not $entry) { throw '安装包没有根目录 Config.daml。' }
  $reader = New-Object System.IO.StreamReader($entry.Open())
  try { [xml]$daml = $reader.ReadToEnd() } finally { $reader.Dispose() }
  $addinId = $daml.ArcGIS.AddInInfo.id
  if ($addinId -ne '{83A6E23E-58CE-4B92-BC7A-7C5012A8BC31}') { throw '请选择统一补充耕地验收插件安装包。' }
  $workspace = $daml.SelectSingleNode("//*[local-name()='dockPane' and @id='AcceptanceAddin_Workspace']")
  if (-not $workspace -or -not $archive.GetEntry('Install/tools.json')) { throw '安装包缺少统一工作台或AI工具目录；未移动旧插件。' }
  $addinName = [System.IO.Path]::GetFileName($PackagePath)
  $registrationRoot = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'ArcGIS/AddIns/ArcGISPro'
  $expected = Join-Path $registrationRoot ("{0}\{1}" -f $addinId, $addinName)
}
finally { $archive.Dispose() }
$registration = Start-Process -FilePath $register -ArgumentList ('"'+(Resolve-Path -LiteralPath $PackagePath).Path+'" /s') -WindowStyle Hidden -Wait -PassThru
if ($registration.ExitCode -ne 0) { throw '统一插件注册失败；未移动旧AI插件。' }
$sourceHash = (Get-FileHash -LiteralPath $PackagePath -Algorithm SHA256).Hash
$deadline = (Get-Date).AddSeconds(20)
$installedHash = $null
do {
  if (Test-Path $expected) { $installedHash = (Get-FileHash -LiteralPath $expected -Algorithm SHA256).Hash }
  if ($sourceHash -eq $installedHash) { break }
  Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)
if ($sourceHash -ne $installedHash) { throw "RegisterAddIn.exe 未将当前包完整写入用户级目录：$expected" }
$oldAiRoot = Join-Path $registrationRoot '{D4CB6CC4-610D-4BDA-9A94-AC49CE8E45F2}'
$archiveRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../_archive/unified-acceptance-20261028'))
$archived = @()
if (Test-Path -LiteralPath $oldAiRoot) {
  foreach ($oldPackage in Get-ChildItem -LiteralPath $oldAiRoot -Filter '*.esriAddInX' -File) {
    $destination = Join-Path $archiveRoot $oldPackage.Name
    if (Test-Path -LiteralPath $destination) { throw "旧插件归档位置已存在，未覆盖：$destination" }
    New-Item -Path $archiveRoot -ItemType Directory -Force | Out-Null
    Move-Item -LiteralPath $oldPackage.FullName -Destination $destination -ErrorAction Stop
    $archived += $destination
  }
}
[pscustomobject]@{Installed=$expected;SHA256=$sourceHash;ArchivedSeparateAI=$archived;RestartRequired=$true} | ConvertTo-Json
