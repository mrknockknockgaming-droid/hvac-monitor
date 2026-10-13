# Deploy or update the Fullscope cloud in its Proxmox LXC, from this PC (Windows PowerShell 5.1).
# Packs the committed hvac-cloud\ and deploy\lxc\ (git archive HEAD), copies it to the Proxmox
# host with scp and runs pve-deploy.sh there over ssh. Asks for the Proxmox root password twice.
#
# First install (fixed IP for the container, copies this PC's database across):
#   .\deploy\lxc\deploy.ps1 -Proxmox 192.168.1.10 -Ip 192.168.1.50/24 -Gateway 192.168.1.1 -WithData
# Update after pulling new code (keeps the container's data):
#   .\deploy\lxc\deploy.ps1 -Proxmox 192.168.1.10
param(
  [Parameter(Mandatory = $true)] [string]$Proxmox,   # the Proxmox host's address
  [int]$CtId = 120,                                   # container ID on Proxmox
  [string]$Ip = "",                                   # first install only: e.g. 192.168.1.50/24
  [string]$Gateway = "",                              # first install only: your router, e.g. 192.168.1.1
  [string]$Storage = "local-lvm",                     # Proxmox storage for the container disk
  [switch]$WithData                                   # copy this PC's hvac-cloud\dev.db into the container
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$cloud = Join-Path $root "hvac-cloud"
$work = Join-Path $env:TEMP "fullscope-deploy"
New-Item -ItemType Directory -Force -Path $work | Out-Null
$bundle = Join-Path $work "fullscope.tar.gz"

$dirty = git -C $root status --porcelain -- hvac-cloud deploy/lxc
if ($dirty) { Write-Warning "Uncommitted changes in hvac-cloud or deploy\lxc are NOT deployed (only committed code is):`n$($dirty -join "`n")" }

Write-Host "== Packing $(git -C $root rev-parse --short HEAD)"
git -C $root archive --format=tar.gz -o $bundle HEAD hvac-cloud deploy/lxc
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }

$files = @($bundle)
$extra = "--ctid $CtId --storage $Storage"
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
          "rm -f /root/fullscope-import.db"
ssh -t "root@$Proxmox" $remote
if ($LASTEXITCODE -ne 0) { throw "install on Proxmox failed (see the messages above)" }
