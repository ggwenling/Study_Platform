$ErrorActionPreference = 'Stop'

function Assert([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Assert-Throws([scriptblock]$Action, [string]$Message, [string]$ExpectedText) {
    $failure = $null
    try { & $Action } catch { $failure = $_ }
    if (-not $failure) { throw $Message }
    if ($ExpectedText -and $failure.Exception.Message -notlike "*$ExpectedText*") {
        throw "$Message Unexpected error: $($failure.Exception.Message)"
    }
}

function Add-TestZipEntry($Archive, [string]$Name, [string]$Text) {
    $entry = $Archive.CreateEntry($Name)
    $stream = $entry.Open()
    $writer = [IO.StreamWriter]::new($stream, [Text.Encoding]::UTF8)
    try { $writer.Write($Text) } finally { $writer.Dispose(); $stream.Dispose() }
}

function Read-ZipEntryText($Entry) {
    $stream = $Entry.Open()
    $reader = [IO.StreamReader]::new($stream, [Text.Encoding]::UTF8, $true)
    try { return $reader.ReadToEnd() } finally { $reader.Dispose(); $stream.Dispose() }
}

function New-TamperedBackup([string]$SourcePath, [string]$DestinationPath) {
    $inputFile = [IO.File]::OpenRead($SourcePath)
    $inputArchive = [IO.Compression.ZipArchive]::new($inputFile, [IO.Compression.ZipArchiveMode]::Read, $false)
    $outputFile = [IO.File]::Open($DestinationPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $outputArchive = [IO.Compression.ZipArchive]::new($outputFile, [IO.Compression.ZipArchiveMode]::Create, $false)
    try {
        foreach ($entry in $inputArchive.Entries) {
            $copy = $outputArchive.CreateEntry($entry.FullName)
            if ($entry.FullName -eq 'storage/note.txt') {
                $stream = $copy.Open()
                $writer = [IO.StreamWriter]::new($stream, [Text.Encoding]::UTF8)
                try { $writer.Write('tampered content') } finally { $writer.Dispose(); $stream.Dispose() }
            } elseif (-not ($entry.FullName.EndsWith('/') -or $entry.FullName.EndsWith('\'))) {
                $source = $entry.Open()
                $destination = $copy.Open()
                try { $source.CopyTo($destination) } finally { $destination.Dispose(); $source.Dispose() }
            }
        }
    } finally {
        $outputArchive.Dispose()
        $inputArchive.Dispose()
        $inputFile.Dispose()
    }
}

$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('synapse-backup-restore-tests-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
try {
    . (Join-Path $PSScriptRoot '..\backup-restore.ps1')
    $ProjectRoot = $testRoot
    $RuntimeDir = Join-Path $ProjectRoot '.local\runtime'
    $StateFile = Join-Path $RuntimeDir 'processes.json'
    $storagePath = Join-Path $ProjectRoot '.local\storage'
    New-Item -ItemType Directory -Path $storagePath -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $ProjectRoot '.env'), @'
DATABASE_URL=postgresql+psycopg://fixture_user:fixture_password@127.0.0.1:59999/backup_restore_test
STORAGE_DIR=.local/storage
MODEL_CONNECTIONS_KEY=fixture-model-key-not-real-731
'@, [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $storagePath 'note.txt'), 'storage-before-backup')
    [IO.File]::WriteAllText((Join-Path $storagePath '.env'), 'must-not-be-copied')
    [IO.File]::WriteAllText((Join-Path $storagePath '.preview.tmp'), 'temporary-file')
    $privateKeyNames = @('id_rsa', 'id_ed25519', 'provider-private.pem', 'server.key', 'client.p12', 'client.pfx', 'private.pkcs8', 'putty.ppk', 'rsa.der')
    foreach ($name in $privateKeyNames) {
        [IO.File]::WriteAllText((Join-Path $storagePath $name), 'private-key-fixture')
    }

    $script:MockDbPath = Join-Path $testRoot 'mock-database-state.txt'
    $script:ProcessFixture = @()
    $script:FailPgRestore = $false
    $script:FailGrant = $false
    $script:GrantSql = $null
    [IO.File]::WriteAllText($script:MockDbPath, 'database-before-backup')

    function Get-CimInstance {
        param([string]$ClassName, [string]$Filter, [string]$ErrorAction)
        if ($Filter -match '^ProcessId = (\d+)$') {
            return @($script:ProcessFixture | Where-Object { $_.ProcessId -eq [int]$Matches[1] } | Select-Object -First 1)
        }
        return @($script:ProcessFixture)
    }

    function Invoke-PgTool {
        param([string]$ToolName, [string[]]$Arguments, $Connection)
        if ($ToolName -eq 'psql') {
            $command = $Arguments | Where-Object { $_ -like '--command=*' } | Select-Object -First 1
            if ($command -like '*SELECT version_num*') { return @('isolated_revision') }
            $script:GrantSql = $command
            if ($script:FailGrant) { throw 'simulated grant failure' }
            return @()
        }
        if ($ToolName -eq 'pg_dump') {
            $fileArgument = $Arguments | Where-Object { $_ -like '--file=*' } | Select-Object -First 1
            if (-not $fileArgument) { throw 'pg_dump stub did not receive --file.' }
            $dumpPath = $fileArgument.Substring('--file='.Length)
            [IO.File]::WriteAllText($dumpPath, [IO.File]::ReadAllText($script:MockDbPath))
            return @()
        }
        if ($ToolName -eq 'pg_restore') {
            if ($script:FailPgRestore) { throw 'simulated pg_restore failure' }
            $dumpPath = $Arguments[-1]
            [IO.File]::WriteAllText($script:MockDbPath, [IO.File]::ReadAllText($dumpPath))
            return @()
        }
        throw "Unexpected PostgreSQL tool: $ToolName"
    }

    function Get-StudyTableCount([string]$DumpPath) {
        Assert (Test-Path -LiteralPath $DumpPath) 'dry-run table count should read the extracted isolated dump'
        return 3
    }

    $runtimeLockPath = Join-Path $RuntimeDir 'control.lock'
    Invoke-WithRuntimeLock {
        Assert (Test-Path -LiteralPath $runtimeLockPath -PathType Leaf) 'runtime lock should be inside the isolated .local/runtime directory'
        Assert-Throws {
            [IO.File]::Open($runtimeLockPath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        } 'runtime lock should be exclusive' ''
    }

    $Action = 'Backup'
    $BackupPath = $null
    $DryRun = $false
    $Force = $false
    $SkipPreBackup = $false
    $Online = $true
    $null = Invoke-Backup
    $backup = Get-ChildItem -LiteralPath (Join-Path $ProjectRoot '.local\backups') -Filter 'synapse-backup-*.zip' |
        Select-Object -First 1
    Assert ($backup -and $backup.Length -gt 0) 'backup should create an archive in the isolated sandbox'

    $zipFile = [IO.File]::OpenRead($backup.FullName)
    $zip = [IO.Compression.ZipArchive]::new($zipFile, [IO.Compression.ZipArchiveMode]::Read, $false)
    try {
        $names = @($zip.Entries | ForEach-Object { $_.FullName })
        Assert (-not ($names | Where-Object { $_ -match '(^|/)\.env(?:\.|$)' })) 'archive must not include .env files'
        foreach ($name in $privateKeyNames) {
            Assert (-not ($names -contains "storage/$name")) "archive must not include private-key file $name"
        }
        Assert ($names -contains 'study.dump') 'archive should contain the study schema dump'
        $joinedText = @($zip.Entries | Where-Object { -not $_.FullName.EndsWith('/') } | ForEach-Object { Read-ZipEntryText $_ }) -join [Environment]::NewLine
        Assert (-not $joinedText.Contains('fixture-model-key-not-real-731')) 'archive must not contain the model connection key'
        Assert (-not $joinedText.Contains('fixture_password')) 'archive must not contain the database password'
    } finally {
        $zip.Dispose()
        $zipFile.Dispose()
    }

    $verified = Read-VerifiedBackup $backup.FullName
    try {
        Assert ($verified.Manifest.file_count -eq 2) 'manifest should inventory the dump and one storage file'
        Assert ($verified.Manifest.alembic_revision -eq 'isolated_revision') 'manifest should record the Alembic revision'
        Assert ($verified.Manifest.model_connections_key_fingerprint -eq (Get-KeyFingerprint 'fixture-model-key-not-real-731')) 'manifest should record only the key fingerprint'
        Assert (Test-Path -LiteralPath (Join-Path $verified.Root 'storage\note.txt')) 'verified archive should extract storage files'
    } finally {
        Remove-Item -LiteralPath $verified.Root -Recurse -Force
    }

    $tamperedPath = Join-Path $testRoot 'tampered.zip'
    New-TamperedBackup $backup.FullName $tamperedPath
    Assert-Throws { Read-VerifiedBackup $tamperedPath } 'tampered archive should be rejected' 'SHA-256'
    Assert-Throws { ConvertTo-PgConnection 'postgresql://fixture:secret@127.0.0.1:5432/backup_restore_test?dbname=other_db' } 'libpq database query overrides should be rejected' 'can override the configured database target'
    $validEnv = [IO.File]::ReadAllText((Join-Path $ProjectRoot '.env'))
    [IO.File]::WriteAllText((Join-Path $ProjectRoot '.env'), $validEnv.Replace('STORAGE_DIR=.local/storage', 'STORAGE_DIR=..\unrelated-repository-folder'))
    Assert-Throws { Read-EnvSettings } 'storage outside .local should be rejected' 'dedicated directory beneath'
    [IO.File]::WriteAllText((Join-Path $ProjectRoot '.env'), $validEnv.Replace('STORAGE_DIR=.local/storage', 'STORAGE_DIR=.local/backups'))
    Assert-Throws { Read-EnvSettings } 'the backup folder cannot be used as storage' 'STORAGE_DIR must not overlap'
    [IO.File]::WriteAllText((Join-Path $ProjectRoot '.env'), $validEnv.Replace('STORAGE_DIR=.local/storage', 'STORAGE_DIR=.local/runtime'))
    Assert-Throws { Read-EnvSettings } 'the runtime folder cannot be used as storage' 'STORAGE_DIR must not overlap'
    [IO.File]::WriteAllText((Join-Path $ProjectRoot '.env'), $validEnv)

    $traversalPath = Join-Path $testRoot 'traversal.zip'
    $traversalStream = [IO.File]::Open($traversalPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $traversalZip = [IO.Compression.ZipArchive]::new($traversalStream, [IO.Compression.ZipArchiveMode]::Create, $false)
    try {
        $manifest = [pscustomobject]@{
            format_version = 1
            database_name = 'backup_restore_test'
            file_count = 2
            files = @(
                [pscustomobject]@{ path = 'study.dump'; sha256 = ('0' * 64) },
                [pscustomobject]@{ path = '../outside.txt'; sha256 = ('0' * 64) }
            )
        } | ConvertTo-Json -Depth 4
        Add-TestZipEntry $traversalZip 'manifest.json' $manifest
        Add-TestZipEntry $traversalZip 'study.dump' 'fixture dump'
        $traversalZip.CreateEntry('storage/') | Out-Null
        Add-TestZipEntry $traversalZip '../outside.txt' 'must-not-extract'
    } finally { $traversalZip.Dispose() }
    Assert-Throws { Read-VerifiedBackup $traversalPath } 'path traversal archive should be rejected' 'unsafe archive member'

    for ($index = 0; $index -lt $privateKeyNames.Count; $index++) {
        $privateKeyPath = Join-Path $testRoot "private-key-$index.zip"
        $privateKeyStream = [IO.File]::Open($privateKeyPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $privateKeyZip = [IO.Compression.ZipArchive]::new($privateKeyStream, [IO.Compression.ZipArchiveMode]::Create, $false)
        try { Add-TestZipEntry $privateKeyZip ("storage/$($privateKeyNames[$index])") 'private-key-fixture' } finally { $privateKeyZip.Dispose() }
        Assert-Throws { Read-VerifiedBackup $privateKeyPath } "private-key archive member $($privateKeyNames[$index]) should be rejected" 'private-key filename'
    }

    $DryRun = $true
    $BackupPath = $backup.FullName
    $beforeDryRunDb = [IO.File]::ReadAllText($script:MockDbPath)
    $beforeDryRunStorage = [IO.File]::ReadAllText((Join-Path $storagePath 'note.txt'))
    $dryRunOutput = @(Invoke-Restore) -join [Environment]::NewLine
    Assert ($dryRunOutput -like '*3 study table(s)*') 'dry run should report the study table count'
    Assert ($dryRunOutput -like "*target database 'backup_restore_test'*") 'dry run should report the target database'
    Assert ($dryRunOutput -like '*2 archive file(s)*1 storage file(s)*') 'dry run should report archive and storage file counts'
    Assert ([IO.File]::ReadAllText($script:MockDbPath) -eq $beforeDryRunDb) 'dry run must not change database state'
    Assert ([IO.File]::ReadAllText((Join-Path $storagePath 'note.txt')) -eq $beforeDryRunStorage) 'dry run must not change storage'

    $script:ProcessFixture = @([pscustomobject]@{
        ProcessId = 51234
        CreationDate = [DateTime]::UtcNow
        ExecutablePath = Join-Path $ProjectRoot 'backend\.venv\Scripts\python.exe'
        CommandLine = 'python.exe -m study_platform.cli.worker'
    })
    Assert (@(Get-RunningSynapseComponents) -contains 'worker') 'unmanaged worker process for the sandbox should be detected'
    Assert-Throws { Assert-ServicesStopped } 'restore must refuse while a worker process is running' 'Restore refused'
    $script:ProcessFixture = @()

    [IO.File]::WriteAllText($script:MockDbPath, 'database-after-backup')
    [IO.File]::WriteAllText((Join-Path $storagePath 'note.txt'), 'storage-after-backup')
    [IO.File]::WriteAllText((Join-Path $storagePath 'new-file.txt'), 'new file')
    $DryRun = $false
    $Force = $true
    $SkipPreBackup = $true
    $null = Invoke-Restore
    Assert ([IO.File]::ReadAllText($script:MockDbPath) -eq 'database-before-backup') 'restore should recover the isolated database dump'
    Assert ([IO.File]::ReadAllText((Join-Path $storagePath 'note.txt')) -eq 'storage-before-backup') 'restore should recover storage content'
    Assert (-not (Test-Path -LiteralPath (Join-Path $storagePath 'new-file.txt'))) 'restore should replace storage rather than merge it'
    Assert (-not (Test-Path -LiteralPath (Join-Path $storagePath '.env'))) 'restore should not create excluded .env files'
    Assert (-not (Test-Path -LiteralPath (Join-Path $storagePath '.preview.tmp'))) 'restore should not create excluded temporary files'
    Assert ($script:GrantSql -match 'GRANT USAGE ON SCHEMA study' -and $script:GrantSql -match 'ALTER DEFAULT PRIVILEGES IN SCHEMA study') 'restore should reapply only study access grants'
    Assert ($script:GrantSql -notmatch 'study_graph|public') 'restore grants must not touch other schemas'

    [IO.File]::WriteAllText($script:MockDbPath, 'database-before-failed-restore')
    [IO.File]::WriteAllText((Join-Path $storagePath 'note.txt'), 'storage-before-failed-restore')
    $script:FailPgRestore = $true
    Assert-Throws { Invoke-Restore } 'failed database restore should surface an error' 'simulated pg_restore failure'
    Assert ([IO.File]::ReadAllText($script:MockDbPath) -eq 'database-before-failed-restore') 'failed database restore should leave database state untouched'
    Assert ([IO.File]::ReadAllText((Join-Path $storagePath 'note.txt')) -eq 'storage-before-failed-restore') 'failed database restore should roll storage back'

    $script:FailPgRestore = $false
    $script:FailGrant = $true
    [IO.File]::WriteAllText($script:MockDbPath, 'database-before-grant-failure')
    [IO.File]::WriteAllText((Join-Path $storagePath 'note.txt'), 'storage-before-grant-failure')
    Assert-Throws { Invoke-Restore } 'grant failure should be reported after the data restore' 'application access grants failed'
    Assert ([IO.File]::ReadAllText($script:MockDbPath) -eq 'database-before-backup') 'grant failure must leave the restored database in place'
    Assert ([IO.File]::ReadAllText((Join-Path $storagePath 'note.txt')) -eq 'storage-before-backup') 'grant failure must leave matching restored storage in place'
    Assert (@(Get-ChildItem -LiteralPath (Split-Path -Parent $storagePath) -Directory -Filter '.storage.old-*').Count -eq 1) 'grant failure should retain the previous storage directory for recovery'

    Write-Output 'PASS: isolated backup, manifest/hash checks, .env exclusion, safe extraction, process guard, target guards, dry run, restore round trip, grants and failure safety'
} finally {
    $resolved = [IO.Path]::GetFullPath($testRoot)
    $tempPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolved.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase) -and
        (Test-Path -LiteralPath $resolved)) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
