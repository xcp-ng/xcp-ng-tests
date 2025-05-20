#Requires -RunAsAdministrator

[CmdletBinding()]
param (
    [Parameter()]
    [switch]$NoNetReporting,
    [Parameter()]
    [switch]$NoCleanup,
    [Parameter()]
    [switch]$WithTools
)

$ErrorActionPreference = "Stop"

if (!(Test-Path "$PSScriptRoot\id_rsa.pub")) {
    throw "Cannot find id_rsa.pub for SSH configuration"
}

if ($WithTools) {
    Read-Host -Prompt "Did you install PV tools manually?"
}

# Sometimes enabling updates will disrupt installation and rebooting. So disable that.
Write-Output "Disabling updates"
Set-ItemProperty -Path HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU -Name NoAutoUpdate -Type DWord -Value 1 -Force
Set-ItemProperty -Path HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU -Name AUOptions -Type DWord -Value 2 -Force

Write-Output "Installing SSH"
$exitCode = (Start-Process -Wait msiexec.exe -ArgumentList "/i `"$PSScriptRoot\OpenSSH-Win64-v9.8.3.0.msi`" /passive /norestart" -PassThru).ExitCode
if ($exitCode -ne 0) {
    throw
}
Copy-Item "$PSScriptRoot\id_rsa.pub" "$env:ProgramData\ssh\administrators_authorized_keys" -Force
icacls.exe "$env:ProgramData\ssh\administrators_authorized_keys" /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
if ($LASTEXITCODE -ne 0) {
    throw
}
New-NetFirewallRule -Action Allow -Program "$env:ProgramFiles\OpenSSH\sshd.exe" -Direction Inbound -Protocol TCP -LocalPort 22 -DisplayName sshd

Write-Output "Installing Git Bash"
$exitCode = (Start-Process -Wait "$PSScriptRoot\Git-2.49.0-64-bit.exe" -ArgumentList "/silent" -PassThru).ExitCode
if ($exitCode -ne 0) {
    throw
}
Set-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Type String -Value "$env:ProgramFiles\Git\bin\bash.exe" -Force

Write-Output "Disabling automatic disk optimization"
schtasks /change /disable /tn "\Microsoft\Windows\Defrag\ScheduledDefrag"
if ($exitCode -ne 0) {
    throw
}

if (!$NoNetReporting) {
    Write-Output "Installing network reporting script"
    Copy-Item "$PSScriptRoot\netreport.ps1" "$env:SystemDrive\" -Force
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-executionpolicy bypass $env:SystemDrive\netreport.ps1"
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\SYSTEM" -RunLevel Highest
    $task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal
    Register-ScheduledTask -InputObject $task -TaskName "XCP-ng Test Network Report"
}

if (!$NoCleanup) {
    Read-Host -Prompt "Unplug Internet, run Disk Cleanup and continue"
    # You should check at least "Temporary files" in Disk Cleanup

    Write-Output "Cleaning up component store"
    dism.exe /Online /Cleanup-Image /StartComponentCleanup /ResetBase

    Write-Output "Cleaning up SoftwareDistribution"
    Stop-Service wuauserv, BITS
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "$env:windir\SoftwareDistribution\Download\*"

    Write-Output "Cleaning up Defender signatures"
    & "$env:ProgramFiles\Windows Defender\MpCmdRun.exe" -RemoveDefinitions -All
}

Write-Output "Resealing"
Stop-Process -Name sysprep -ErrorAction SilentlyContinue
if ($WithTools) {
    # WS2025 eval only allows 1 rearm, save this for later
    Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SoftwareProtectionPlatform" -Name SkipRearm -Type DWord -Value 1
    & "$env:windir\System32\Sysprep\sysprep.exe" "/generalize" "/oobe" "/shutdown" "/unattend:$PSScriptRoot\unattend-persisthw.xml"
}
else {
    & "$env:windir\System32\Sysprep\sysprep.exe" "/generalize" "/oobe" "/shutdown" "/unattend:$PSScriptRoot\unattend.xml"
}
