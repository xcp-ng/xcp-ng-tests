[CmdletBinding()]
param (
    [Parameter(Mandatory, ParameterSetName = "Drivers")]
    [string]$DriverPath,
    [Parameter(Mandatory, ParameterSetName = "Msi")]
    [string]$MsiPath,
    [Parameter(ParameterSetName = "Msi")]
    [string[]]$Patches,
    [Parameter(ParameterSetName = "Msi")]
    [switch]$WaitXenServerInstallAgent
)

$ErrorActionPreference = "Stop"

$signature = @'
[DllImport("Cfgmgr32.dll")]
public static extern uint CMP_WaitNoPendingInstallEvents(uint dwTimeout);
public const uint INFINITE = 0xFFFFFFFF;
public const uint WAIT_OBJECT_0 = 0;
public const uint WAIT_TIMEOUT = 258;
public const uint WAIT_FAILED = 0xFFFFFFFF;
'@

$nativeMethods = Add-Type -MemberDefinition $signature -Name NativeMethods -Namespace XenTools -PassThru

if ($DriverPath) {
    foreach ($driver in @("xenbus", "xeniface", "xenvbd", "xenvif", "xennet")) {
        $infPath = (Resolve-Path "$DriverPath\$driver\x64\$driver.inf").Path
        Write-Output "Attempting install $infPath"
        pnputil.exe /add-driver $infPath /install
        if ($LASTEXITCODE -ne 0) {
            throw "pnputil.exe $LASTEXITCODE"
        }
    }
}
elseif ($MsiPath) {
    $resolvedMsiPath = (Resolve-Path $MsiPath).Path
    Write-Output "Attempting install $resolvedMsiPath"
    $msiexecProcess = Start-Process -Wait -PassThru msiexec.exe -ArgumentList "/i", "$resolvedMsiPath", "ALLOWAUTOUPDATE=NO", "/l*vx", "C:\other-install.log", "/passive", "/norestart"
    if ($msiexecProcess.ExitCode -ne 0 -and $msiexecProcess.ExitCode -ne 1641 -and $msiexecProcess.ExitCode -ne 3010) {
        throw "msiexec.exe $($msiexecProcess.ExitCode)"
    }

    foreach ($patch in $Patches) {
        $resolvedPatchPath = (Resolve-Path $patch).Path
        $msiexecProcess = Start-Process -Wait -PassThru msiexec.exe -ArgumentList "/update", "$resolvedPatchPath", "/l*vx+", "C:\other-patch.log", "/passive", "/norestart"
        if ($msiexecProcess.ExitCode -ne 0 -and $msiexecProcess.ExitCode -ne 1641 -and $msiexecProcess.ExitCode -ne 3010) {
            throw "msiexec.exe $($msiexecProcess.ExitCode)"
        }
    }
}

# Some installers like XCP-ng 8.2 don't install their drivers using MSI but through their own service (XenInstall).
# The drivers themselves also need time to detect devices and set up xenfilt.
# In any case, leave some time for the installation to do its thing.
Start-Sleep -Seconds 15

Write-Output "Waiting for install events"
$nativeMethods::CMP_WaitNoPendingInstallEvents($nativeMethods::INFINITE)

if ($WaitXenServerInstallAgent) {
    # The XenServer InstallAgent may reload NICs among other things until it marks the installation as done.
    # With /norestart, we should wait for it to reaches NeedsReboot or Installed, it won't reboot by itself.
    Write-Output "Waiting for XenServer InstallAgent"
    $deadline = (Get-Date).AddMinutes(5)
    do {
        Start-Sleep -Seconds 10
        $status = (Get-ItemProperty `
            -Path HKLM:\SOFTWARE\XenServer\XenTools\InstallAgent `
            -Name InstallStatus `
            -ErrorAction SilentlyContinue).InstallStatus
    } while ($status -notin @("NeedsReboot", "Installed") -and (Get-Date) -lt $deadline)
    Write-Output "XenServer InstallAgent status: $status"
}
