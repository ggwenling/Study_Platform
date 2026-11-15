$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\local-runtime.ps1')
$RuntimeDir = Join-Path $ProjectRoot ('.local\runtime-process-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $RuntimeDir | Out-Null
$record = $null
$other = $null
try {
    $probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $probe.Start()
    $port = $probe.LocalEndpoint.Port
    $probe.Stop()
    $python = Join-Path $ProjectRoot 'backend\.venv\Scripts\python.exe'
    $record = Start-Component 'probe' $python "-u -m http.server $port --bind 127.0.0.1" (Join-Path $ProjectRoot 'backend')
    $listener = $null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($listener) { break }
        Start-Sleep -Milliseconds 100
    }
    if (-not $listener) { throw 'probe server did not bind' }
    if (-not (Test-OwnedListener $record $listener.OwningProcess)) { throw 'venv child listener was not recognized' }
    $other = Start-Component 'unrelated' $python '-u -c "import time; time.sleep(30)"' (Join-Path $ProjectRoot 'backend')
    if (Test-OwnedListener $other $listener.OwningProcess) { throw 'unrelated process adopted the listener' }
    Stop-OwnedProcess $record
    Start-Sleep -Milliseconds 300
    if (Get-OwnedProcess $record) { throw 'managed process survived stop' }
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw 'child listener leaked after stop' }
    Write-Output 'PASS: real venv process identity, child listener ownership and descendant cleanup'
} finally {
    if ($record) { Stop-OwnedProcess $record }
    if ($other) { Stop-OwnedProcess $other }
    $resolved = [IO.Path]::GetFullPath($RuntimeDir)
    $allowed = [IO.Path]::GetFullPath((Join-Path $ProjectRoot '.local')) + [IO.Path]::DirectorySeparatorChar
    if ($resolved.StartsWith($allowed) -and (Test-Path -LiteralPath $resolved)) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
