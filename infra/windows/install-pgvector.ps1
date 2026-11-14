param(
    [string]$PGROOT = 'C:\Program Files\PostgreSQL\18',
    [string]$Source = (Join-Path $PSScriptRoot '..\..\.local\pgvector'),
    [string]$Installer = (Join-Path $PSScriptRoot '..\..\.local\vs_BuildTools.exe'),
    [string]$ServiceName = 'postgresql-x64-18'
)
$ErrorActionPreference = 'Stop'
$localDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..\.local')).Path
$log = Join-Path $localDir 'pgvector-install.log'
$status = Join-Path $localDir 'pgvector-install-status.json'
function Set-Status([string]$Step, [string]$Message) {
    @{ step = $Step; message = $Message; updated = (Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -Encoding UTF8 $status
}
Start-Transcript -Path $log -Force | Out-Null
try {
    Set-Status 'checking' 'Checking prerequisites'
    if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator privileges are required.' }
    Set-Status 'starting-service' 'Starting the existing PostgreSQL service'
    $service = Get-Service -Name $ServiceName
    if ($service.Status -ne 'Running') { Start-Service -Name $ServiceName }
    (Get-Service -Name $ServiceName).WaitForStatus('Running', [TimeSpan]::FromSeconds(60))
    $PGROOT = (Resolve-Path -LiteralPath $PGROOT).Path
    $Source = (Resolve-Path -LiteralPath $Source).Path
    foreach ($path in @('bin\pg_config.exe', 'include\server\postgres.h', 'lib\postgres.lib', 'share\extension')) {
        if (-not (Test-Path -LiteralPath (Join-Path $PGROOT $path))) { throw "Missing PostgreSQL file or directory: $path" }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Source 'Makefile.win'))) { throw 'Source must contain pgvector Makefile.win.' }
    if ((Test-Path -LiteralPath (Join-Path $PGROOT 'lib\vector.dll')) -or (Test-Path -LiteralPath (Join-Path $PGROOT 'share\extension\vector.control'))) { throw 'Existing pgvector files detected; inspect before replacing.' }
    & (Join-Path $PGROOT 'bin\pg_config.exe') --version
    if ($LASTEXITCODE -ne 0) { throw 'pg_config failed.' }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    $devcmd = $null
    if (Test-Path -LiteralPath $vswhere) {
        $vsPath = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        if ($vsPath) { $devcmd = Join-Path ($vsPath | Select-Object -First 1) 'Common7\Tools\VsDevCmd.bat' }
    }
    if (-not $devcmd -or -not (Test-Path -LiteralPath $devcmd)) {
        Set-Status 'installing-build-tools' 'Installing Microsoft Visual Studio C++ Build Tools'
        $Installer = (Resolve-Path -LiteralPath $Installer).Path
        $signature = Get-AuthenticodeSignature -LiteralPath $Installer
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw 'Build Tools installer signature is not valid Microsoft Corporation signature.' }
        $installPath = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools'
        $instances = @()
        if (Test-Path -LiteralPath $vswhere) { $instances = @(& $vswhere -all -products '*' -property installationPath) }
        if ($instances -contains $installPath) {
            Set-Status 'repairing-build-tools' 'Repairing the existing incomplete Build Tools instance'
            $setup = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\setup.exe'
            $process = Start-Process -FilePath $setup -ArgumentList "repair --installPath `"$installPath`" --quiet --norestart" -WindowStyle Hidden -Wait -PassThru
            if ($process.ExitCode -eq 3010) { throw 'Build Tools repair requires a restart; restart manually and rerun.' }
            if ($process.ExitCode -ne 0) { throw "Build Tools repair failed with exit code $($process.ExitCode)." }
            Set-Status 'installing-build-tools' 'Adding required C++ tools to the existing instance'
            $args = "modify --installPath `"$installPath`" --add Microsoft.VisualStudio.Workload.VCTools --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 --add Microsoft.VisualStudio.Component.Windows10SDK.19041 --quiet --norestart"
            $process = Start-Process -FilePath $setup -ArgumentList $args -WindowStyle Hidden -Wait -PassThru
        } else {
            $args = "--installPath `"$installPath`" --add Microsoft.VisualStudio.Workload.VCTools --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 --add Microsoft.VisualStudio.Component.Windows10SDK.19041 --quiet --wait --norestart"
            $process = Start-Process -FilePath $Installer -ArgumentList $args -WindowStyle Hidden -Wait -PassThru
        }
        if ($process.ExitCode -eq 3010) { throw 'Build Tools installation requires a restart; restart manually and rerun.' }
        if ($process.ExitCode -ne 0) { throw "Build Tools installer failed with exit code $($process.ExitCode)." }
        if (-not (Test-Path -LiteralPath $vswhere)) { throw 'vswhere not found after installation.' }
        $vsPath = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        if (-not $vsPath) { throw 'C++ Build Tools not found by vswhere.' }
        $devcmd = Join-Path ($vsPath | Select-Object -First 1) 'Common7\Tools\VsDevCmd.bat'
        if (-not (Test-Path -LiteralPath $devcmd)) { throw 'VS developer command environment not found.' }
    }
    Set-Status 'building' 'Building and installing pgvector'
    $env:PGROOT = $PGROOT
    Push-Location $Source
    try {
        & cmd.exe /d /s /c "`"`"$devcmd`" -arch=x64 -host_arch=x64 && nmake /F Makefile.win && nmake /F Makefile.win install`""
        if ($LASTEXITCODE -ne 0) { throw 'Build or installation failed; inspect target files before retrying.' }
    } finally { Pop-Location }
    foreach ($path in @('lib\vector.dll', 'share\extension\vector.control', 'share\extension\vector--0.8.7.sql')) {
        if (-not (Test-Path -LiteralPath (Join-Path $PGROOT $path))) { throw "Installation did not produce $path" }
    }
    Set-Status 'complete' 'pgvector files installed and PostgreSQL service running; enable extension separately in the intended database.'
    Write-Host 'Installation complete.'
} catch {
    Set-Status 'failed' $_.Exception.Message
    Write-Error $_
    exit 1
} finally { Stop-Transcript | Out-Null }
