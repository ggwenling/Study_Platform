param(
    [ValidateSet('start', 'stop', 'status')][string]$Action = 'start',
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$RuntimeDir = Join-Path $ProjectRoot '.local\runtime'
$StateFile = Join-Path $RuntimeDir 'processes.json'

function Get-OwnedProcess($Record) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($Record.pid)"
    if ($process -and $process.CreationDate.ToUniversalTime().ToString('o') -eq $Record.created -and
        $process.ExecutablePath -eq $Record.executable -and $process.CommandLine -eq $Record.command) {
        return $process
    }
    return $null
}

function Get-ProcessRecord($Process) {
    return [pscustomobject]@{
        pid = $Process.ProcessId
        created = $Process.CreationDate.ToUniversalTime().ToString('o')
        executable = $Process.ExecutablePath
        command = $Process.CommandLine
    }
}

function Test-OwnedListener($Record, [int]$ListenerPid) {
    $root = Get-OwnedProcess $Record
    if (-not $root) { return $false }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ListenerPid"
    while ($process -and $process.CreationDate -ge $root.CreationDate) {
        if ($process.ProcessId -eq $root.ProcessId) { return $true }
        if ($process.ParentProcessId -eq $process.ProcessId) { break }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.ParentProcessId)"
    }
    return $false
}

function Stop-OwnedProcess($Record) {
    $process = Get-OwnedProcess $Record
    if (-not $process) { return }
    # Capture and verify each descendant's identity; never kill by port or image name.
    foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($process.ProcessId)")) {
        if ($child.CreationDate -ge $process.CreationDate) { Stop-OwnedProcess (Get-ProcessRecord $child) }
    }
    if (Get-OwnedProcess $Record) { Stop-Process -Id $Record.pid -Force -ErrorAction SilentlyContinue }
}

function Test-ApiReady {
    try {
        $result = Invoke-RestMethod 'http://127.0.0.1:8000/health/ready' -TimeoutSec 2
        return $result.status -eq 'ready'
    } catch { return $false }
}

function Start-Component([string]$Name, [string]$Executable, [string]$Arguments, [string]$Directory) {
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $Directory `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $RuntimeDir "$Name.out.log") `
        -RedirectStandardError (Join-Path $RuntimeDir "$Name.err.log")
    $native = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)"
    if (-not $native -or -not $native.CommandLine) { throw "$Name exited during startup; see .local/runtime/$Name.err.log" }
    return Get-ProcessRecord $native
}

function Invoke-LocalRuntime {
    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
    # Serialize start/stop/status so two double-clicks cannot create duplicate children.
    try { $lock = [IO.File]::Open((Join-Path $RuntimeDir 'control.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw 'Another runtime command is in progress; retry shortly.' }
    try {
        $state = @{}
        if (Test-Path -LiteralPath $StateFile) {
            $saved = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
            foreach ($entry in $saved.PSObject.Properties) { $state[$entry.Name] = $entry.Value }
        }
        if ($Action -eq 'status') {
            foreach ($name in @('api', 'worker', 'web')) {
                $running = $state.ContainsKey($name) -and $null -ne (Get-OwnedProcess $state[$name])
                Write-Output "$name process: $(if ($running) { 'running' } else { 'not managed or stopped' })"
            }
            Write-Output "API ready (database, migration, storage, keys configured): $(Test-ApiReady)"
            Write-Output 'Readiness does not validate model balance or worker progress. Logs: .local/runtime/'
            return
        }
        if ($Action -eq 'stop') {
            foreach ($name in @('web', 'worker', 'api')) {
                if ($state.ContainsKey($name)) { Stop-OwnedProcess $state[$name] }
            }
            if (Test-Path -LiteralPath $StateFile) { Remove-Item -LiteralPath $StateFile }
            Write-Output 'Stopped managed application processes. PostgreSQL was left running.'
            return
        }

        $python = Join-Path $ProjectRoot 'backend\.venv\Scripts\python.exe'
        $vite = Join-Path $ProjectRoot 'frontend\node_modules\vite\bin\vite.js'
        if (-not (Test-Path -LiteralPath $python)) { throw 'Run uv sync --locked in backend first (Python 3.12).' }
        if (-not (Test-Path -LiteralPath $vite)) { throw 'Run npm ci in frontend first.' }
        if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.env'))) { throw 'Configure the root .env using README.md first.' }
        $node = (Get-Command node.exe -ErrorAction Stop).Source
        # Preflight every port before creating anything. Existing unmanaged servers are never adopted.
        foreach ($pair in @(@('api', 8000), @('web', 5173))) {
            $owned = $state.ContainsKey($pair[0]) -and $null -ne (Get-OwnedProcess $state[$pair[0]])
            $listeners = @(Get-NetTCPConnection -LocalPort $pair[1] -State Listen -ErrorAction SilentlyContinue)
            if ($listeners.Count -and (-not $owned -or @($listeners | Where-Object { -not (Test-OwnedListener $state[$pair[0]] $_.OwningProcess) }).Count)) {
                throw "Port $($pair[1]) is in use by an unmanaged process. Stop that server yourself or run status; no process was killed."
            }
        }
        $started = @()
        try {
            foreach ($component in @(
                @('api', $python, '-u -m study_platform.cli.serve', 'backend'),
                @('worker', $python, '-u -m study_platform.cli.worker', 'backend'),
                @('web', $node, ('"' + $vite + '" --host 127.0.0.1 --port 5173 --strictPort'), 'frontend')
            )) {
                $name = $component[0]
                if (-not $state.ContainsKey($name) -or -not (Get-OwnedProcess $state[$name])) {
                    $record = Start-Component $name $component[1] $component[2] (Join-Path $ProjectRoot $component[3])
                    $state[$name] = $record
                    $started += $name
                    $state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $StateFile -Encoding UTF8
                }
            }
            $ready = $false
            for ($attempt = 0; $attempt -lt 30; $attempt++) {
                foreach ($name in @('api', 'worker', 'web')) {
                    if (-not (Get-OwnedProcess $state[$name])) { throw "$name exited; see .local/runtime/$name.err.log" }
                }
                if (Test-ApiReady) {
                    try {
                        $web = Invoke-WebRequest 'http://127.0.0.1:5173/' -UseBasicParsing -TimeoutSec 2
                        if ($web.StatusCode -eq 200) { $ready = $true; break }
                    } catch { }
                }
                Start-Sleep -Milliseconds 500
            }
            if (-not $ready) { throw 'Startup not ready. Check PostgreSQL, .env, migrations and .local/runtime logs; follow README.md. No setup or migration was run.' }
            Write-Output 'Ready: http://127.0.0.1:5173/ (API and worker running)'
            if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:5173/' -WindowStyle Hidden }
        } catch {
            foreach ($name in $started) { Stop-OwnedProcess $state[$name]; $state.Remove($name) }
            $state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $StateFile -Encoding UTF8
            throw
        }
    } finally { $lock.Dispose() }
}

# Dot sourcing loads helpers for isolated process-ownership tests without starting services.
if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-LocalRuntime }
    catch { Write-Error $_ -ErrorAction Continue; exit 1 }
}
