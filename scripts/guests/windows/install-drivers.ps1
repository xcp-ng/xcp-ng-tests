[CmdletBinding()]
param (
    [Parameter(Mandatory, ParameterSetName = "Drivers")]
    [string]$DriverPath,
    [Parameter(Mandatory, ParameterSetName = "Msi")]
    [string]$MsiPath,
    [Parameter()]
    [switch]$Shutdown
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
    $msiexecProcess = Start-Process -Wait -PassThru msiexec.exe -ArgumentList "/i", "$resolvedMsiPath", "/l*", "C:\other-install.log", "/passive", "/norestart"
    if ($msiexecProcess.ExitCode -ne 0 -and $msiexecProcess.ExitCode -ne 1641 -and $msiexecProcess.ExitCode -ne 3010) {
        throw "msiexec.exe $($msiexecProcess.ExitCode)"
    }
}

# Some installers like XCP-ng 8.2 don't install their drivers using MSI but through their own service (XenInstall).
# The drivers themselves also need time to detect devices and set up xenfilt.
# In any case, leave some time for the installation to do its thing.
Start-Sleep -Seconds 15

Write-Output "Waiting for install events"
$nativeMethods::CMP_WaitNoPendingInstallEvents($nativeMethods::INFINITE)

if ($Shutdown) {
    Write-Output "Shutting down"
    Start-Sleep -Seconds 5
    Stop-Computer -Force
}
