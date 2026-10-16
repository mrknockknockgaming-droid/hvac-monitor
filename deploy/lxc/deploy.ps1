# Deploy or update the Fullscope cloud in its Proxmox LXC, from this PC (Windows PowerShell 5.1).
# Packs the committed hvac-cloud\ and deploy\lxc\ (git archive HEAD), builds the committed C#
# server (dotnet\, as a self-contained Linux program; needs the .NET 10 SDK), copies both to the
# Proxmox host with scp and runs pve-deploy.sh there over ssh. Asks for the Proxmox root password twice.
#
# First install (fixed IP for the container, copies this PC's database across):
#   .\deploy\lxc\deploy.ps1 -Proxmox 192.168.1.10 -Ip 192.168.1.50/24 -Gateway 192.168.1.1 -WithData
# Update after pulling new code (keeps the container's data):
#   .\deploy\lxc\deploy.ps1 -Proxmox 192.168.1.10
# Go back to the Python version (same data; run without -Python to return to C#):
#   .\deploy\lxc\deploy.ps1 -Proxmox 192.168.1.10 -Python
param(
  [Parameter(Mandatory = $true)] [string]$Proxmox,   # the Proxmox host's address
  [int]$CtId = 120,                                   # container ID on Proxmox
  [string]$Ip = "",                                   # first install only: e.g. 192.168.1.50/24
  [string]$Gateway = "",                              # first install only: your router, e.g. 192.168.1.1
  [string]$Storage = "local-lvm",                     # Proxmox storage for the container disk
  [switch]$WithData,                                  # copy this PC's hvac-cloud\dev.db into the container
  [switch]$Python                                     # run the Python version instead of the C# build
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$cloud = Join-Path $root "hvac-cloud"
$work = Join-Path $env:TEMP "fullscope-deploy"
New-Item -ItemType Directory -Force -Path $work | Out-Null
$bundle = Join-Path $work "fullscope.tar.gz"

$dirty = git -C $root status --porcelain -- hvac-cloud deploy/lxc dotnet
if ($dirty) { Write-Warning "Uncommitted changes in hvac-cloud, deploy\lxc or dotnet are NOT deployed (only committed code is):`n$($dirty -join "`n")" }

Write-Host "== Packing $(git -C $root rev-parse --short HEAD)"
git -C $root archive --format=tar.gz -o $bundle HEAD hvac-cloud deploy/lxc
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }

$files = @($bundle)
$extra = "--ctid $CtId --storage $Storage"
if ($Python) {
  $extra += " --python"
} else {
  if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw "The .NET 10 SDK is needed to build the server: winget install Microsoft.DotNet.SDK.10 (then open a new PowerShell), or deploy the Python version with -Python"
  }
  Write-Host "== Building the C# server for Linux (committed code)"
  $src = Join-Path $work "src"
  $publish = Join-Path $work "publish"
  if (Test-Path $src) { Remove-Item -Recurse -Force $src }
  if (Test-Path $publish) { Remove-Item -Recurse -Force $publish }
  New-Item -ItemType Directory -Force -Path $src | Out-Null
  $srcTar = Join-Path $work "src.tar"
  git -C $root archive --format=tar -o $srcTar HEAD dotnet hvac-cloud/hvaccloud/refrigerant_tables.json
  if ($LASTEXITCODE -ne 0) { throw "git archive of dotnet failed" }
  tar -xf $srcTar -C $src
  if ($LASTEXITCODE -ne 0) { throw "unpacking the source failed" }
  dotnet publish (Join-Path $src "dotnet\src\Fullscope.Cloud") -c Release -r linux-x64 --self-contained `
    -p:PublishSingleFile=true -p:EnableCompressionInSingleFile=true -p:DebugType=none -o $publish --nologo -v quiet
  if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed" }
  $bin = Join-Path $work "fullscope-bin.tar.gz"
  tar -czf $bin -C $publish .
  if ($LASTEXITCODE -ne 0) { throw "packing the build failed" }
  $files += $bin
  $extra += " --bin /root/fullscope-bin.tar.gz"
}
if ($Ip) { $extra += " --ip $Ip" }
if ($Gateway) { $extra += " --gw $Gateway" }
if ($WithData) {
  Write-Host "== Taking a consistent copy of hvac-cloud\dev.db (close the PC's ingest window first so nothing is missed)"
  & (Join-Path $cloud ".venv\Scripts\python.exe") (Join-Path $cloud "manage.py") backup
  if ($LASTEXITCODE -ne 0) { throw "manage.py backup failed" }
  $db = Get-ChildItem (Join-Path $cloud "backups\dev-*.db") | Sort-Object LastWriteTime | Select-Object -Last 1
  $dbCopy = Join-Path $work "fullscope-import.db"
  Copy-Item $db.FullName $dbCopy -Force
  $files += $dbCopy
  $extra += " --db /root/fullscope-import.db"
}

Write-Host "== Copying to root@${Proxmox}:/root/"
scp $files "root@${Proxmox}:/root/"
if ($LASTEXITCODE -ne 0) { throw "scp failed" }

Write-Host "== Installing in container $CtId"
$remote = "set -e; rm -rf /root/fullscope-deploy; mkdir -p /root/fullscope-deploy; " +
          "tar -xzf /root/fullscope.tar.gz -C /root/fullscope-deploy deploy/lxc/pve-deploy.sh; " +
          "bash /root/fullscope-deploy/deploy/lxc/pve-deploy.sh /root/fullscope.tar.gz $extra; " +
          "rm -f /root/fullscope-import.db /root/fullscope-bin.tar.gz"
ssh -t "root@$Proxmox" $remote
if ($LASTEXITCODE -ne 0) { throw "install on Proxmox failed (see the messages above)" }
