param(
    [int]$LocalPort = 15432
)

$ErrorActionPreference = "Stop"

function Test-PortOpen {
    param([int]$Port)
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $iar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(500, $false)) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

if (Test-PortOpen -Port $LocalPort) {
    Write-Host "ops db tunnel already listening on 127.0.0.1:$LocalPort"
    exit 0
}

$dbHost = (& ssh -n -o BatchMode=yes rider-ops-ec2 "sudo -n docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' rider-db-1").Trim()
if (-not $dbHost) { throw "Could not resolve rider-db-1 container IP" }

$forward = "127.0.0.1:${LocalPort}:$($dbHost):5432"
Start-Process -FilePath "ssh" -ArgumentList @("-o", "BatchMode=yes", "-N", "-L", $forward, "rider-ops-ec2") -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 2
if (-not (Test-PortOpen -Port $LocalPort)) { throw "SSH tunnel did not open on 127.0.0.1:$LocalPort" }
Write-Host "ops db tunnel ready on 127.0.0.1:$LocalPort"
