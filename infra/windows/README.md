# pgvector for PostgreSQL on Windows

The installer builds pgvector v0.8.7 against an existing PostgreSQL installation, starts the existing PostgreSQL service, and installs the extension files. It does not create or modify any database. The process requires administrator privileges, but does not automatically restart Windows.

Obtain the [Microsoft Visual Studio 2022 Build Tools bootstrapper](https://aka.ms/vs/17/release/vs_BuildTools.exe) as `.local/vs_BuildTools.exe` and the official pgvector source as `.local/pgvector`:

```powershell
git clone --depth 1 --branch v0.8.7 https://github.com/pgvector/pgvector.git D:\Synapse\.local\pgvector
```

Keep `.local/` Git-ignored. Start the installer from PowerShell and approve the Windows UAC prompt:

```powershell
Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File "D:\Synapse\infra\windows\install-pgvector.ps1"'
```

The script accepts `-PGROOT`, `-Source`, `-Installer`, and `-ServiceName`. It checks the installer's Microsoft signature before installing C++ Build Tools if needed, repairs an incomplete registered Build Tools instance before adding required components if present, uses the VS x64 development environment to compile and install, and refuses to overwrite an existing `vector.dll` or `vector.control`. Progress and errors are in `.local/pgvector-install-status.json`, and the full log is `.local/pgvector-install.log`. If installation fails after copying files, inspect the target before retrying. If Build Tools requires a restart, restart manually before retrying.

Separately, connect to each intended database with appropriate privileges and run `CREATE EXTENSION vector;`, then verify `SELECT extversion FROM pg_extension WHERE extname = 'vector';`. No credentials belong in the installer or its logs.

Official references: [pgvector Windows installation](https://github.com/pgvector/pgvector#windows), [Visual Studio Build Tools components](https://learn.microsoft.com/en-us/visualstudio/install/workload-component-id-vs-build-tools).
