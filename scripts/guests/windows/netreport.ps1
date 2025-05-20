do {
    Start-Sleep -Seconds 2
    $Adapter = Get-NetAdapter -Physical | Where-Object Status -eq Up | Select-Object -First 1
} while (!$Adapter)

$Port = [System.IO.Ports.SerialPort]::new("COM1")
try {
    $Port.Open()
    for ($i = 0; $i -lt 300; $i++) {
        $Address = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $Adapter.InterfaceIndex
        if ($Address) {
            # write the full `r`n sequence so that grep could catch it as a full line
            $ReportString = "~xcp-ng-tests~$($Adapter.MacAddress)=$($Address.IPv4Address)~end~`r`n"
            $Port.Write($ReportString)
        }
        Start-Sleep -Seconds 1
    }
    $Port.Close()
}
finally {
    $Port.Dispose()
}
