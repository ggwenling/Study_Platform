param(
    [ValidateSet('Backup', 'Restore')][string]$Action = 'Backup',
    [string]$BackupPath,
    [switch]$DryRun,
    [switch]$Force,
    [switch]$SkipPreBackup,
    [switch]$Online,
    [string]$ProjectRoot
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Join-Path $PSScriptRoot '..\..'
}
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$RuntimeDir = Join-Path $ProjectRoot '.local\runtime'
$StateFile = Join-Path $RuntimeDir 'processes.json'
$MaxArchiveBytes = [long]100GB

Add-Type -AssemblyName System.IO.Compression

function Read-EnvSettings {
    $envPath = Join-Path $ProjectRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { throw 'Configure the root .env using README.md first.' }
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $envPath) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $value = $Matches[2]
            if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[$value.Length - 1] -eq '"') -or
                    ($value[0] -eq "'" -and $value[$value.Length - 1] -eq "'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            $values[$Matches[1]] = $value
        }
    }
    if ([string]::IsNullOrWhiteSpace($values['DATABASE_URL'])) { throw '.env must define DATABASE_URL.' }

    $storageValue = $values['STORAGE_DIR']
    if ([string]::IsNullOrWhiteSpace($storageValue)) { $storageValue = '.local\storage' }
    $storagePath = if ([IO.Path]::IsPathRooted($storageValue)) {
        [IO.Path]::GetFullPath($storageValue)
    } else {
        [IO.Path]::GetFullPath((Join-Path $ProjectRoot $storageValue))
    }
    $projectPath = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $localPath = [IO.Path]::GetFullPath((Join-Path $ProjectRoot '.local')).TrimEnd('\')
    $backupPath = [IO.Path]::GetFullPath((Join-Path $ProjectRoot '.local\backups'))
    $runtimePath = [IO.Path]::GetFullPath((Join-Path $ProjectRoot '.local\runtime'))
    $storagePrefix = $storagePath.TrimEnd('\') + '\'
    $localPrefix = $localPath + '\'
    $backupPrefix = $backupPath.TrimEnd('\') + '\'
    $runtimeNormalized = $runtimePath.TrimEnd('\')
    $backupNormalized = $backupPath.TrimEnd('\')
    if ($storagePath.TrimEnd('\') -eq $projectPath -or $projectPath.StartsWith($storagePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'STORAGE_DIR must not be the project root or one of its parent directories.'
    }
    if (-not $storagePath.StartsWith($localPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'STORAGE_DIR must be a dedicated directory beneath the project .local folder.'
    }
    if ($storagePath.TrimEnd('\') -eq $backupNormalized -or
        $storagePath.StartsWith($backupPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        $backupPath.StartsWith($storagePrefix, [StringComparison]::OrdinalIgnoreCase) -or
        $storagePath.TrimEnd('\') -eq $runtimeNormalized -or
        $storagePath.StartsWith($runtimePath.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $runtimePath.StartsWith($storagePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'STORAGE_DIR must not overlap .local/backups or .local/runtime.'
    }
    Assert-NoReparseComponents $storagePath $localPath

    return [pscustomobject]@{
        DatabaseUrl = $values['DATABASE_URL']
        MigrationDatabaseUrl = $values['MIGRATION_DATABASE_URL']
        StoragePath = $storagePath
        ModelConnectionsKey = $values['MODEL_CONNECTIONS_KEY']
    }
}

function Assert-NoReparseComponents([string]$Path, [string]$Boundary) {
    $boundaryPath = [IO.Path]::GetFullPath($Boundary).TrimEnd('\')
    $currentPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $boundaryPrefix = $boundaryPath + '\'
    if ($currentPath -ne $boundaryPath -and -not $currentPath.StartsWith($boundaryPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Path is outside its allowed local directory.'
    }
    while ($true) {
        if (Test-Path -LiteralPath $currentPath) {
            $item = Get-Item -LiteralPath $currentPath -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Path contains a reparse point; refusing to follow it: $currentPath"
            }
        }
        if ($currentPath -eq $boundaryPath) { break }
        $currentPath = Split-Path -Parent $currentPath
        if ([string]::IsNullOrWhiteSpace($currentPath)) { throw 'Could not resolve path boundary.' }
    }
}

function ConvertTo-PgConnection([string]$DatabaseUrl) {
    $normalized = [regex]::Replace($DatabaseUrl, '^postgresql\+psycopg2?(?=://)', 'postgresql', 'IgnoreCase')
    $normalized = [regex]::Replace($normalized, '^postgres://', 'postgresql://', 'IgnoreCase')
    if ($normalized -notmatch '^postgresql://') { throw 'DATABASE_URL must use a PostgreSQL URI.' }
    try { $uri = [uri]$normalized } catch { throw 'DATABASE_URL is not a valid PostgreSQL URI.' }
    if ([string]::IsNullOrWhiteSpace($uri.Host) -or $uri.Host.Contains(',')) { throw 'DATABASE_URL must include one PostgreSQL host.' }
    if (-not [string]::IsNullOrEmpty($uri.Fragment)) { throw 'DATABASE_URL must not include a URI fragment.' }
    foreach ($part in $uri.Query.TrimStart('?').Split('&')) {
        if ([string]::IsNullOrWhiteSpace($part)) { continue }
        $key = [uri]::UnescapeDataString(($part -split '=', 2)[0].Replace('+', ' '))
        if ($key -match '^(?i:dbname|service|host|hostaddr|port|user|username|password|passfile|servicefile)$') {
            throw "DATABASE_URL query parameter '$key' can override the configured database target; remove it."
        }
    }
    $databaseName = [uri]::UnescapeDataString($uri.AbsolutePath.Trim('/'))
    if ([string]::IsNullOrWhiteSpace($databaseName) -or $databaseName.Contains('/')) {
        throw 'DATABASE_URL must identify one PostgreSQL database.'
    }

    $userName = $null
    $password = $null
    if (-not [string]::IsNullOrEmpty($uri.UserInfo)) {
        $separator = $uri.UserInfo.IndexOf(':')
        if ($separator -lt 0) {
            $userName = [uri]::UnescapeDataString($uri.UserInfo)
        } else {
            $userName = [uri]::UnescapeDataString($uri.UserInfo.Substring(0, $separator))
            $password = [uri]::UnescapeDataString($uri.UserInfo.Substring($separator + 1))
        }
    }
    $builder = [UriBuilder]::new($uri)
    $builder.Password = ''
    return [pscustomobject]@{
        DatabaseName = $databaseName
        Host = $uri.Host
        Port = $uri.Port
        ConnectionString = $builder.Uri.AbsoluteUri
        Password = $password
        UserName = $userName
    }
}

function Get-DatabaseConnection($Settings) {
    $applicationConnection = ConvertTo-PgConnection $Settings.DatabaseUrl
    if ([string]::IsNullOrWhiteSpace($Settings.MigrationDatabaseUrl)) { return $applicationConnection }
    $migrationConnection = ConvertTo-PgConnection $Settings.MigrationDatabaseUrl
    if ($migrationConnection.DatabaseName -ne $applicationConnection.DatabaseName -or
        $migrationConnection.Port -ne $applicationConnection.Port -or
        $migrationConnection.Host -ne $applicationConnection.Host) {
        throw 'MIGRATION_DATABASE_URL must target the same host, port, and database as DATABASE_URL.'
    }
    return $migrationConnection
}

function Quote-SqlIdentifier([string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Name)) { throw 'DATABASE_URL must include a user name to restore application grants.' }
    return '"' + $Name.Replace('"', '""') + '"'
}

function Grant-ApplicationAccess($Settings, $MigrationConnection) {
    $applicationConnection = ConvertTo-PgConnection $Settings.DatabaseUrl
    $role = Quote-SqlIdentifier $applicationConnection.UserName
    $sql = "GRANT USAGE ON SCHEMA study TO $role; GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA study TO $role; GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA study TO $role; ALTER DEFAULT PRIVILEGES IN SCHEMA study GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO $role; ALTER DEFAULT PRIVILEGES IN SCHEMA study GRANT USAGE, SELECT ON SEQUENCES TO $role;"
    $null = Invoke-PgTool 'psql' @('--single-transaction', '--set=ON_ERROR_STOP=on', ('--command=' + $sql)) $MigrationConnection
}

function Invoke-PgTool([string]$ToolName, [string[]]$Arguments, $Connection) {
    $command = Get-Command ($ToolName + '.exe') -ErrorAction Stop
    $allArguments = @(('--dbname=' + $Connection.ConnectionString), '--no-password') + $Arguments
    $hadPassword = Test-Path Env:\PGPASSWORD
    $previousPassword = $env:PGPASSWORD
    try {
        if ($null -ne $Connection.Password -and $Connection.Password -ne '') {
            $env:PGPASSWORD = $Connection.Password
        } elseif ($hadPassword) {
            Remove-Item Env:\PGPASSWORD
        }
        $output = & $command.Source @allArguments 2>&1
        if ($LASTEXITCODE -ne 0) { throw "$ToolName failed with exit code $LASTEXITCODE." }
        return @($output | ForEach-Object { $_.ToString() })
    } finally {
        if ($hadPassword) { $env:PGPASSWORD = $previousPassword } else { Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue }
    }
}

function Get-KeyFingerprint([string]$Key) {
    if ([string]::IsNullOrWhiteSpace($Key)) { return $null }
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($Key)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant().Substring(0, 12)
    } finally { $sha.Dispose() }
}

function Get-DatabaseRevision($Connection) {
    $result = Invoke-PgTool 'psql' @('-At', '--command=SELECT version_num FROM study.alembic_version LIMIT 1') $Connection
    return (($result | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1) -as [string])
}

function Get-StudyTableCount([string]$DumpPath) {
    $command = Get-Command 'pg_restore.exe' -ErrorAction Stop
    $output = & $command.Source '--list' '--schema=study' $DumpPath 2>&1
    if ($LASTEXITCODE -ne 0) {
        $details = (@($output | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine).Trim()
        throw "pg_restore could not read the study dump table list. $details"
    }
    return @($output | Where-Object { $_.ToString() -match '^\s*\d+;\s+\d+\s+\d+\s+TABLE\s+study(?:\s|$)' }).Count
}

function Get-OwnedProcess($Record) {
    if ($null -eq $Record -or $null -eq $Record.pid) { return $null }
    $process = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $($Record.pid)" -ErrorAction Stop
    if ($process -and $process.CreationDate.ToUniversalTime().ToString('o') -eq $Record.created -and
        $process.ExecutablePath -eq $Record.executable -and $process.CommandLine -eq $Record.command) {
        return $process
    }
    return $null
}

function Get-RunningSynapseComponents {
    $running = [Collections.Generic.List[string]]::new()
    if (Test-Path -LiteralPath $StateFile -PathType Leaf) {
        $state = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
        foreach ($name in @('api', 'worker')) {
            $property = $state.PSObject.Properties[$name]
            if ($property -and $property.Value -and (Get-OwnedProcess $property.Value)) { $running.Add($name) }
        }
    }

    $projectPrefix = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\') + '\'
    foreach ($process in @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop)) {
        $commandLine = [string]$process.CommandLine
        $executable = [string]$process.ExecutablePath
        if ([string]::IsNullOrWhiteSpace($commandLine)) { continue }
        $isProjectProcess = $executable.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase) -or
            $commandLine.IndexOf($ProjectRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0
        if (-not $isProjectProcess) { continue }
        foreach ($component in @('api', 'worker')) {
            $module = if ($component -eq 'api') { 'study_platform\.cli\.serve' } else { 'study_platform\.cli\.worker' }
            if ($commandLine -match $module -and -not $running.Contains($component)) { $running.Add($component) }
        }
    }
    return @($running)
}

function Assert-BackupProcessState([switch]$AllowOnline) {
    $running = @(Get-RunningSynapseComponents)
    if ($running.Count -eq 0 -or $AllowOnline) { return }
    Write-Warning "Synapse $($running -join ' and ') process(es) are running. A hot backup may capture inconsistent database and file state."
    $answer = Read-Host 'Enter N to cancel, stop with Stop-Synapse.cmd, and retry; enter Y to continue online'
    if ($answer -notmatch '^(?i:y|yes)$') { throw 'Backup cancelled. Run Stop-Synapse.cmd and retry, or pass -Online to allow a hot backup.' }
}

function Assert-ServicesStopped {
    $running = @(Get-RunningSynapseComponents)
    if ($running.Count -gt 0) {
        throw "Restore refused while Synapse $($running -join ' and ') process(es) are running. Run Stop-Synapse.cmd first."
    }
}

function Invoke-WithRuntimeLock([scriptblock]$ActionBlock) {
    $localPath = Join-Path $ProjectRoot '.local'
    Assert-NoReparseComponents $RuntimeDir $localPath
    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
    Assert-NoReparseComponents $RuntimeDir $localPath

    $lockPath = Join-Path $RuntimeDir 'control.lock'
    $resolvedRuntime = [IO.Path]::GetFullPath($RuntimeDir).TrimEnd('\')
    $resolvedLock = [IO.Path]::GetFullPath($lockPath)
    if ((Split-Path -Parent $resolvedLock).TrimEnd('\') -ne $resolvedRuntime) {
        throw 'Runtime lock path is outside .local/runtime.'
    }
    if (Test-Path -LiteralPath $lockPath) {
        $lockItem = Get-Item -LiteralPath $lockPath -Force
        if ($lockItem.PSIsContainer -or ($lockItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Runtime lock path must be a regular file inside .local/runtime.'
        }
    }
    try {
        $lock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch {
        throw 'Another runtime command is in progress; retry after it finishes.'
    }
    try { & $ActionBlock } finally { $lock.Dispose() }
}

function Test-ExcludedStorageName([string]$Name) {
    return $Name -match '^(?i:\.env(?:\.|$))' -or $Name -match '(?i)(?:\.tmp|\.part)$' -or
        $Name -match '^(?i:(?:id_rsa|id_dsa|id_ecdsa|id_ed25519|id_x25519|id_sk)(?:$|\.))' -or
        $Name -match '(?i)(?:private|secret)[_-]?key' -or
        $Name -match '(?i)\.(?:key|pem|p12|pfx|p8|pkcs8|ppk|der)$'
}

function Copy-StorageChildren([string]$Source, [string]$Destination) {
    foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
        if (Test-ExcludedStorageName $item.Name) { continue }
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Storage contains a reparse point; refusing to follow it: $($item.FullName)"
        }
        $target = Join-Path $Destination $item.Name
        if ($item.PSIsContainer) {
            New-Item -ItemType Directory -Path $target -Force | Out-Null
            Copy-StorageChildren $item.FullName $target
        } else {
            Copy-Item -LiteralPath $item.FullName -Destination $target
        }
    }
}

function New-WorkDirectory {
    $workPath = Join-Path ([IO.Path]::GetTempPath()) ('synapse-backup-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $workPath | Out-Null
    return $workPath
}

function Get-RelativePayloadPath([string]$Root, [string]$Path) {
    return $Path.Substring($Root.TrimEnd('\').Length).TrimStart('\').Replace('\', '/')
}

function Get-PayloadInventory([string]$Root) {
    $inventory = @()
    foreach ($file in Get-ChildItem -LiteralPath $Root -File -Recurse -Force) {
        $relative = Get-RelativePayloadPath $Root $file.FullName
        if ($relative -match '(^|/)(?i:\.env(?:\.|$))') { throw 'Refusing to put an .env file in the backup.' }
        $inventory += [pscustomobject]@{
            path = $relative
            sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    return @($inventory | Sort-Object -Property path)
}

function Add-ArchiveFile($Archive, [string]$SourcePath, [string]$EntryName) {
    $entry = $Archive.CreateEntry($EntryName.Replace('\', '/'), [IO.Compression.CompressionLevel]::Optimal)
    $inputStream = [IO.File]::OpenRead($SourcePath)
    $outputStream = $entry.Open()
    try { $inputStream.CopyTo($outputStream) } finally { $outputStream.Dispose(); $inputStream.Dispose() }
}

function Add-StorageToArchive($Archive, [string]$Directory, [string]$Prefix) {
    foreach ($item in Get-ChildItem -LiteralPath $Directory -Force) {
        if (Test-ExcludedStorageName $item.Name) { continue }
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Storage contains a reparse point; refusing to follow it: $($item.FullName)"
        }
        $entryName = $Prefix + $item.Name
        if ($item.PSIsContainer) {
            $Archive.CreateEntry($entryName + '/') | Out-Null
            Add-StorageToArchive $Archive $item.FullName ($entryName + '/')
        } else {
            Add-ArchiveFile $Archive $item.FullName $entryName
        }
    }
}

function New-BackupArchive($Settings, $Connection, [string]$DestinationPath) {
    $destinationParent = Split-Path -Parent $DestinationPath
    $localPath = Join-Path $ProjectRoot '.local'
    Assert-NoReparseComponents $destinationParent $localPath
    New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
    $workPath = New-WorkDirectory
    try {
        $storageStage = Join-Path $workPath 'storage'
        $dumpPath = Join-Path $workPath 'study.dump'
        New-Item -ItemType Directory -Path $storageStage | Out-Null
        if (Test-Path -LiteralPath $Settings.StoragePath) {
            $storageItem = Get-Item -LiteralPath $Settings.StoragePath -Force
            if (-not $storageItem.PSIsContainer) { throw 'STORAGE_DIR exists but is not a directory.' }
            if (($storageItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'STORAGE_DIR is a reparse point; refusing to follow it.' }
            Copy-StorageChildren $Settings.StoragePath $storageStage
        }

        $revision = Get-DatabaseRevision $Connection
        $null = Invoke-PgTool 'pg_dump' @('--schema=study', '--format=custom', '--no-owner', '--no-privileges', ('--file=' + $dumpPath)) $Connection
        $inventory = Get-PayloadInventory $workPath
        if (@($inventory | Where-Object { $_.path -eq 'study.dump' }).Count -ne 1) { throw 'pg_dump did not produce study.dump.' }
        $manifest = [pscustomobject]@{
            format_version = 1
            created_at_utc = [DateTime]::UtcNow.ToString('o')
            database_name = $Connection.DatabaseName
            alembic_revision = $revision
            file_count = $inventory.Count
            files = @($inventory)
            model_connections_key_fingerprint = Get-KeyFingerprint $Settings.ModelConnectionsKey
        }
        $manifestPath = Join-Path $workPath 'manifest.json'
        [IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))

        if (Test-Path -LiteralPath $DestinationPath) { throw "Backup already exists: $DestinationPath" }
        $fileStream = [IO.File]::Open($DestinationPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $archive = [IO.Compression.ZipArchive]::new($fileStream, [IO.Compression.ZipArchiveMode]::Create, $false)
        try {
            Add-ArchiveFile $archive $manifestPath 'manifest.json'
            Add-ArchiveFile $archive $dumpPath 'study.dump'
            $archive.CreateEntry('storage/') | Out-Null
            Add-StorageToArchive $archive $storageStage 'storage/'
        } finally {
            $archive.Dispose()
        }
        return $DestinationPath
    } finally {
        if (Test-Path -LiteralPath $workPath) { Remove-Item -LiteralPath $workPath -Recurse -Force }
    }
}

function Get-SafeArchivePath([string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Name)) { throw 'Backup contains an empty archive member name.' }
    $path = $Name.Replace('\', '/')
    if ($path.StartsWith('/') -or $path -match '^[A-Za-z]:' -or $path -match '[\x00-\x1f]') {
        throw "Backup contains an unsafe archive member: $Name"
    }
    $segments = @($path.TrimEnd('/').Split('/'))
    if ($segments.Count -eq 0 -or @($segments | Where-Object { $_ -eq '' -or $_ -eq '.' -or $_ -eq '..' -or $_.Contains(':') }).Count -gt 0) {
        throw "Backup contains an unsafe archive member: $Name"
    }
    if (@($segments | Where-Object { $_ -match '^(?i:\.env(?:\.|$))' }).Count -gt 0) {
        throw 'Backup contains an .env member, which is never allowed.'
    }
    if ($segments[0] -eq 'storage' -and @($segments | Select-Object -Skip 1 | Where-Object {
        $_ -match '^(?i:(?:id_rsa|id_dsa|id_ecdsa|id_ed25519|id_x25519|id_sk)(?:$|\.))' -or
        $_ -match '(?i)(?:private|secret)[_-]?key' -or
        $_ -match '(?i)\.(?:key|pem|p12|pfx|p8|pkcs8|ppk|der)$'
    }).Count -gt 0) {
        throw 'Backup contains a private-key filename under storage, which is never allowed.'
    }
    return ($segments -join '/')
}

function Read-VerifiedBackup([string]$ArchivePath) {
    if (-not (Test-Path -LiteralPath $ArchivePath -PathType Leaf)) { throw 'BackupPath must identify an existing ZIP file.' }
    $root = Join-Path ([IO.Path]::GetTempPath()) ('synapse-restore-' + [guid]::NewGuid().ToString('N'))
    $stream = [IO.File]::OpenRead($ArchivePath)
    $archive = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Read, $false)
    try {
        $entries = @()
        $entryMap = @{}
        $manifestEntry = $null
        $expandedBytes = [long]0
        foreach ($entry in $archive.Entries) {
            $isDirectory = $entry.FullName.EndsWith('/') -or $entry.FullName.EndsWith('\')
            $safePath = Get-SafeArchivePath $entry.FullName
            if ($entryMap.ContainsKey($safePath)) { throw "Backup contains duplicate archive members: $safePath" }
            $entryMap[$safePath] = $entry
            if ($safePath -ceq 'manifest.json' -and -not $isDirectory) { $manifestEntry = $entry }
            $first = $safePath.Split('/')[0]
            if ($safePath -cne 'manifest.json' -and $first -notin @('study.dump', 'storage')) {
                throw "Backup contains an unexpected archive member: $safePath"
            }
            if ($first -eq 'study.dump' -and $safePath -cne 'study.dump') {
                throw "Backup contains an unexpected study.dump path: $safePath"
            }
            if ($safePath -eq 'study.dump' -and ($isDirectory -or $safePath.Contains('/'))) {
                throw 'Backup study.dump must be a root file.'
            }
            if ($safePath -eq 'storage' -and -not $isDirectory) { throw 'Backup storage root must be a directory.' }
            if ($safePath -eq 'manifest.json' -and $isDirectory) { throw 'Backup manifest.json must be a file.' }
            if ($safePath -ne 'manifest.json' -and -not $isDirectory) {
                $mode = ($entry.ExternalAttributes -shr 16) -band 0xFFFF
                $type = $mode -band 0xF000
                if ($type -ne 0 -and $type -ne 0x8000) { throw "Backup contains a non-regular file: $safePath" }
                $expandedBytes += [long]$entry.Length
                if ($expandedBytes -gt $MaxArchiveBytes) { throw 'Backup expands beyond the 100 GiB safety limit.' }
            } elseif ($isDirectory) {
                $mode = ($entry.ExternalAttributes -shr 16) -band 0xFFFF
                $type = $mode -band 0xF000
                if ($type -ne 0 -and $type -ne 0x4000) { throw "Backup contains a non-directory entry: $safePath" }
            }
            $entries += [pscustomobject]@{ Entry = $entry; Path = $safePath; IsDirectory = $isDirectory }
        }
        if (-not $manifestEntry) { throw 'Backup is missing root manifest.json.' }
        $reader = [IO.StreamReader]::new($manifestEntry.Open(), [Text.Encoding]::UTF8, $true)
        try { $manifest = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
        if ([int]$manifest.format_version -ne 1) { throw 'Backup format_version is missing or unsupported.' }
        if ([string]::IsNullOrWhiteSpace([string]$manifest.database_name)) { throw 'Backup manifest is missing database_name.' }
        if ($null -ne $manifest.model_connections_key_fingerprint -and [string]$manifest.model_connections_key_fingerprint -notmatch '^[a-f0-9]{12}$') {
            throw 'Backup manifest contains an invalid model key fingerprint.'
        }

        $inventory = @{}
        foreach ($item in @($manifest.files)) {
            $path = Get-SafeArchivePath ([string]$item.path)
            if ($path -ne [string]$item.path -or ($path -cne 'study.dump' -and -not $path.StartsWith('storage/', [StringComparison]::Ordinal))) {
                throw "Manifest lists an unsupported file path: $path"
            }
            if ($inventory.ContainsKey($path)) { throw "Manifest lists a duplicate file path: $path" }
            if ([string]$item.sha256 -notmatch '^[a-fA-F0-9]{64}$') { throw "Manifest contains an invalid SHA-256 value for $path" }
            $inventory[$path] = ([string]$item.sha256).ToLowerInvariant()
        }
        if ([int]$manifest.file_count -ne $inventory.Count) { throw 'Backup manifest file_count does not match its inventory.' }
        if (-not $inventory.ContainsKey('study.dump')) { throw 'Backup manifest does not inventory study.dump.' }
        if (-not $entryMap.ContainsKey('storage')) { throw 'Backup is missing the storage directory.' }

        $archiveFiles = @($entries | Where-Object { -not $_.IsDirectory -and $_.Path -ne 'manifest.json' } | ForEach-Object { $_.Path })
        if ($archiveFiles.Count -ne $inventory.Count) { throw 'Backup archive members do not match the manifest inventory.' }
        foreach ($path in $archiveFiles) {
            if (-not $inventory.ContainsKey($path)) { throw "Backup contains an unlisted file: $path" }
        }
        foreach ($path in $inventory.Keys) {
            if (-not $entryMap.ContainsKey($path) -or $entryMap[$path].FullName.EndsWith('/') -or $entryMap[$path].FullName.EndsWith('\')) {
                throw "Manifest file is missing from the archive: $path"
            }
        }

        New-Item -ItemType Directory -Path $root | Out-Null
        $rootFull = [IO.Path]::GetFullPath($root).TrimEnd('\') + '\'
        foreach ($item in $entries) {
            if ($item.Path -eq 'manifest.json') { continue }
            $target = [IO.Path]::GetFullPath((Join-Path $root ($item.Path.Replace('/', '\'))))
            if (-not $target.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Backup member escapes its extraction directory: $($item.Path)"
            }
            if ($item.IsDirectory) {
                New-Item -ItemType Directory -Path $target -Force | Out-Null
                continue
            }
            $parent = Split-Path -Parent $target
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
            $inputStream = $item.Entry.Open()
            $outputStream = [IO.File]::Open($target, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            try { $inputStream.CopyTo($outputStream) } finally { $outputStream.Dispose(); $inputStream.Dispose() }
            $hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($hash -ne $inventory[$item.Path]) { throw "SHA-256 verification failed for $($item.Path)" }
        }
        return [pscustomobject]@{ Manifest = $manifest; Root = $root }
    } catch {
        if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
        throw
    } finally {
        $archive.Dispose()
        $stream.Dispose()
    }
}

function Confirm-RestoreWarning([string]$Message) {
    Write-Warning $Message
    if ($Force) { return }
    $answer = Read-Host 'Enter Y to continue despite this warning'
    if ($answer -notmatch '^(?i:y|yes)$') { throw 'Restore cancelled.' }
}

function Copy-VerifiedStorage([string]$Source, [string]$Destination) {
    New-Item -ItemType Directory -Path $Destination | Out-Null
    Copy-StorageChildren $Source $Destination
}

function Invoke-Backup {
    $settings = Read-EnvSettings
    $connection = Get-DatabaseConnection $settings
    Assert-BackupProcessState -AllowOnline:$Online
    $backupDirectory = Join-Path $ProjectRoot '.local\backups'
    Assert-NoReparseComponents $backupDirectory (Join-Path $ProjectRoot '.local')
    New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
    $backupFile = Join-Path $backupDirectory ('synapse-backup-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss-fff') + '.zip')
    $result = New-BackupArchive $settings $connection $backupFile
    Write-Output "Backup created: $result"
    Write-Output 'The archive contains only the study schema and configured storage files; .env is not included.'
}

function Invoke-Restore {
    if ([string]::IsNullOrWhiteSpace($BackupPath)) { throw 'Restore requires -BackupPath <zip>.' }
    Assert-ServicesStopped
    $settings = Read-EnvSettings
    $connection = Get-DatabaseConnection $settings
    $archivePath = [IO.Path]::GetFullPath($BackupPath)
    if ($archivePath.StartsWith($settings.StoragePath.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'BackupPath must not be inside STORAGE_DIR, because restore replaces that directory.'
    }
    $verified = Read-VerifiedBackup $archivePath
    $restoreSucceeded = $false
    $databaseRestored = $false
    $storageStage = $null
    $oldStorage = $null
    $movedOldStorage = $false
    $switchedStorage = $false
    try {
        if ($verified.Manifest.database_name -ne $connection.DatabaseName) {
            Confirm-RestoreWarning "Backup database '$($verified.Manifest.database_name)' differs from configured target '$($connection.DatabaseName)'."
        }
        $storedFingerprint = [string]$verified.Manifest.model_connections_key_fingerprint
        $currentFingerprint = Get-KeyFingerprint $settings.ModelConnectionsKey
        if ($storedFingerprint -ne $currentFingerprint) {
            Confirm-RestoreWarning 'MODEL_CONNECTIONS_KEY fingerprint differs. Saved model connection credentials will not decrypt after restore; keep the original key or re-enter those connections.'
        }
        if ($DryRun) {
            $storageFiles = @($verified.Manifest.files | Where-Object { ([string]$_.path).StartsWith('storage/', [StringComparison]::Ordinal) }).Count
            $tableCount = Get-StudyTableCount (Join-Path $verified.Root 'study.dump')
            Write-Output "Dry run passed: would restore $tableCount study table(s) from '$($verified.Manifest.database_name)' into target database '$($connection.DatabaseName)', using $($verified.Manifest.file_count) archive file(s) including $storageFiles storage file(s)."
            return
        }

        if (-not $SkipPreBackup) {
            $backupDirectory = Join-Path $ProjectRoot '.local\backups'
            Assert-NoReparseComponents $backupDirectory (Join-Path $ProjectRoot '.local')
            New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
            $preBackup = Join-Path $backupDirectory ('pre-restore-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss-fff') + '.zip')
            $null = New-BackupArchive $settings $connection $preBackup
            Write-Output "Pre-restore backup created: $preBackup"
        }

        if (Test-Path -LiteralPath $settings.StoragePath) {
            $currentStorage = Get-Item -LiteralPath $settings.StoragePath -Force
            if (-not $currentStorage.PSIsContainer -or ($currentStorage.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Configured STORAGE_DIR is not a regular directory; restore will not replace it.'
            }
        }
        $storageParent = Split-Path -Parent $settings.StoragePath
        New-Item -ItemType Directory -Path $storageParent -Force | Out-Null
        $storageLeaf = Split-Path -Leaf $settings.StoragePath
        $storageStage = Join-Path $storageParent ('.' + $storageLeaf + '.restore-' + [guid]::NewGuid().ToString('N'))
        Copy-VerifiedStorage (Join-Path $verified.Root 'storage') $storageStage
        $oldStorage = Join-Path $storageParent ('.' + $storageLeaf + '.old-' + [guid]::NewGuid().ToString('N'))

        try {
            if (Test-Path -LiteralPath $settings.StoragePath) {
                Move-Item -LiteralPath $settings.StoragePath -Destination $oldStorage
                $movedOldStorage = $true
            }
            Move-Item -LiteralPath $storageStage -Destination $settings.StoragePath
            $storageStage = $null
            $switchedStorage = $true

            $dumpPath = Join-Path $verified.Root 'study.dump'
            $null = Invoke-PgTool 'pg_restore' @('--clean', '--if-exists', '--no-owner', '--no-privileges', '--schema=study', '--single-transaction', '--exit-on-error', $dumpPath) $connection
            $databaseRestored = $true
            Grant-ApplicationAccess $settings $connection
            $restoreSucceeded = $true
        } catch {
            $restoreError = $_
            if ($databaseRestored) {
                throw "The study schema and storage were restored, but application access grants failed. Previous storage remains at '$oldStorage'. Error: $($restoreError.Exception.Message)"
            }
            try {
                if ($switchedStorage -and (Test-Path -LiteralPath $settings.StoragePath)) {
                    Remove-Item -LiteralPath $settings.StoragePath -Recurse -Force
                }
                if ($movedOldStorage -and (Test-Path -LiteralPath $oldStorage)) {
                    Move-Item -LiteralPath $oldStorage -Destination $settings.StoragePath
                    $movedOldStorage = $false
                }
            } catch {
                throw "Database restore failed and storage rollback also failed. Previous storage remains at '$oldStorage'. Database error: $($restoreError.Exception.Message); storage error: $($_.Exception.Message)"
            }
            throw $restoreError
        }

        if ($restoreSucceeded -and $movedOldStorage -and (Test-Path -LiteralPath $oldStorage)) {
            try { Remove-Item -LiteralPath $oldStorage -Recurse -Force }
            catch { Write-Warning "Restore succeeded, but the old storage directory could not be removed: $oldStorage" }
        }
        Write-Output "Restore complete for database '$($connection.DatabaseName)'. Start the app with Start-Synapse.cmd and verify the restored data."
    } finally {
        if ($storageStage -and (Test-Path -LiteralPath $storageStage)) { Remove-Item -LiteralPath $storageStage -Recurse -Force }
        if (Test-Path -LiteralPath $verified.Root) { Remove-Item -LiteralPath $verified.Root -Recurse -Force }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        Invoke-WithRuntimeLock {
            if ($Action -eq 'Backup') {
                Invoke-Backup
            } else {
                Invoke-Restore
            }
        }
    } catch {
        Write-Error $_ -ErrorAction Continue
        exit 1
    }
}
