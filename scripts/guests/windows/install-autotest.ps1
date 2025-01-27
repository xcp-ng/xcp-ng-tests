#Requires -RunAsAdministrator

[CmdletBinding()]
param (
    [Parameter()]
    [switch]$NoNetReporting,
    [Parameter()]
    [switch]$Cleanup
)

$ErrorActionPreference = "Stop"

if (!(Test-Path "$PSScriptRoot\id_rsa.pub")) {
    throw "Cannot find id_rsa.pub for SSH configuration"
}

# Sometimes enabling updates will disrupt installation and rebooting.
# This is a temporary measure at most, but Microsoft makes disabling updates really difficult...
Write-Output "Disabling updates"
Set-ItemProperty -Path HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU -Name AUOptions -Type DWord -Value 2 -Force
Stop-Service wuauserv
Set-Service wuauserv -StartupType Disabled

Write-Output "Installing SSH"
$SSHDownloadPath = "$env:TEMP\OpenSSH-Win64-v9.8.1.0.msi"
Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/PowerShell/Win32-OpenSSH/releases/download/v9.8.1.0p1-Preview/OpenSSH-Win64-v9.8.1.0.msi" -OutFile $SSHDownloadPath
$exitCode = (Start-Process -Wait msiexec.exe -ArgumentList "/i", $SSHDownloadPath, "/passive", "/norestart" -PassThru).ExitCode
if ($exitCode -ne 0) {
    throw
}
Remove-Item -Force $SSHDownloadPath -ErrorAction SilentlyContinue
Copy-Item "$PSScriptRoot\id_rsa.pub" "$env:ProgramData\ssh\administrators_authorized_keys" -Force
icacls.exe "$env:ProgramData\ssh\administrators_authorized_keys" /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
if ($LASTEXITCODE -ne 0) {
    throw
}
New-NetFirewallRule -Action Allow -Program "$env:ProgramFiles\OpenSSH\sshd.exe" -Direction Inbound -Protocol TCP -LocalPort 22 -DisplayName sshd

Write-Output "Installing Git Bash"
$GitDownloadPath = "$env:TEMP\Git-2.47.1-64-bit.exe"
Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/Git-2.47.1-64-bit.exe" -OutFile $GitDownloadPath
$exitCode = (Start-Process -Wait $GitDownloadPath -ArgumentList "/silent" -PassThru).ExitCode
if ($exitCode -ne 0) {
    throw
}
Remove-Item -Force $GitDownloadPath -ErrorAction SilentlyContinue
Set-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Type String -Value "$env:ProgramFiles\Git\bin\bash.exe" -Force

if (!$NoNetReporting) {
    Write-Output "Installing network reporting script"
    Copy-Item "$PSScriptRoot\netreport.ps1" "$env:SystemDrive\" -Force
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-executionpolicy bypass $env:SystemDrive\netreport.ps1"
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\SYSTEM" -RunLevel Highest
    $task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal
    Register-ScheduledTask -InputObject $task -TaskName "XCP-ng Test Network Report"
}

if ($Cleanup) {
    Read-Host -Prompt "Unplug Internet, run Disk Cleanup and continue"

    Write-Output "Cleaning up component store"
    dism.exe /Online /Cleanup-Image /StartComponentCleanup /ResetBase

    Write-Output "Cleaning up SoftwareDistribution"
    Stop-Service wuauserv, BITS -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "$env:windir\SoftwareDistribution\Download\*"

    Write-Output "Cleaning up Defender signatures"
    & "$env:ProgramFiles\Windows Defender\MpCmdRun.exe" -RemoveDefinitions -All

    Write-Output "Optimizing system drive"
    defrag.exe $env:SystemDrive /O
}

Write-Output "Resealing"
Stop-Process -Name sysprep -ErrorAction SilentlyContinue
& "$env:windir\System32\Sysprep\sysprep.exe" /generalize /oobe /shutdown /unattend:$PSScriptRoot\unattend.xml
