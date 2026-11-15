$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\local-runtime.ps1')
$RuntimeDir = Join-Path $ProjectRoot ('.local\runtime-test-' + [guid]::NewGuid().ToString('N'))
$StateFile = Join-Path $RuntimeDir 'processes.json'
$NoBrowser = $true
$script:Processes = @{}
$script:Killed = @()
$script:PortBusy = $false
$script:FailWorker = $false
$script:NextPid = 900000

function Assert($Condition, $Message) { if (-not $Condition) { throw $Message } }
function Get-CimInstance($ClassName, $Filter) {
    if ($Filter -match '^ProcessId = (\d+)$') { return $script:Processes[[int]$Matches[1]] }
    return @()
}
function Stop-Process($Id, [switch]$Force, $ErrorAction) { $script:Killed += $Id; $script:Processes.Remove([int]$Id) }
function Get-NetTCPConnection($LocalPort, $State, $ErrorAction) {
    if ($script:PortBusy) { return [pscustomobject]@{ OwningProcess = 100 } }
    return @()
}
function Test-ApiReady { return $true }
function Invoke-WebRequest($Uri, [switch]$UseBasicParsing, $TimeoutSec) { return @{ StatusCode = 200 } }
function Start-Component($Name, $Executable, $Arguments, $Directory) {
    if ($Name -eq 'worker' -and $script:FailWorker) { throw 'simulated launch failure' }
    $script:NextPid++
    $process = [pscustomobject]@{ ProcessId = $script:NextPid; CreationDate = [datetime]::UtcNow; ExecutablePath = $Executable; CommandLine = $Arguments }
    $script:Processes[$process.ProcessId] = $process
    return Get-ProcessRecord $process
}

try {
    $Action = 'start'
    Invoke-LocalRuntime
    Assert ($script:Processes.Count -eq 3) 'must start exactly three components'
    Invoke-LocalRuntime
    Assert ($script:Processes.Count -eq 3) 'duplicate start created more processes'
    $saved = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
    $script:Processes[[int]$saved.worker.pid].CreationDate = [datetime]::UtcNow.AddDays(1)
    $Action = 'stop'
    Invoke-LocalRuntime
    Assert ($script:Killed.Count -eq 2) 'stale PID must not be killed'
    Assert ($script:Processes.ContainsKey([int]$saved.worker.pid)) 'reused PID was killed'
    $script:Processes.Clear()
    $script:Killed = @()
    $script:PortBusy = $true
    $Action = 'start'
    $failed = $false
    try { Invoke-LocalRuntime } catch { $failed = $_.Exception.Message -like '*unmanaged*' }
    Assert $failed 'port conflict must be reported'
    Assert ($script:Processes.Count -eq 0 -and $script:Killed.Count -eq 0) 'port conflict changed unrelated processes'
    $script:PortBusy = $false
    $script:FailWorker = $true
    $failed = $false
    try { Invoke-LocalRuntime } catch { $failed = $_.Exception.Message -eq 'simulated launch failure' }
    Assert $failed 'partial startup failure must be reported'
    Assert ($script:Processes.Count -eq 0) 'partial startup leaked the API process'
    Assert ($script:Killed.Count -eq 1) 'partial startup must clean up only its own API'
    Write-Output 'PASS: duplicate start, stale identity, port conflict and partial startup cleanup'
} finally {
    $resolved = [IO.Path]::GetFullPath($RuntimeDir)
    $allowed = [IO.Path]::GetFullPath((Join-Path $ProjectRoot '.local')) + [IO.Path]::DirectorySeparatorChar
    if ($resolved.StartsWith($allowed) -and (Test-Path -LiteralPath $resolved)) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
