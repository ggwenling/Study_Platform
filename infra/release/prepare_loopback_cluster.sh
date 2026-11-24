#!/bin/bash
# Create or start the dedicated PostgreSQL 16 cluster used by the release drill.
# It listens only on 127.0.0.1:55432. The distro "main" cluster on 5432 is left down.
set -euo pipefail

if pg_lsclusters --no-header | awk '$1=="16" && $2=="main" && $4=="online" { found=1 } END { exit !found }'; then
  echo "refusing: the default 16/main cluster is online on 5432" >&2
  exit 1
fi

if ! pg_lsclusters --no-header | awk '$1=="16" && $2=="releasegate" { found=1 } END { exit !found }'; then
  sudo pg_createcluster 16 releasegate --port=55432 --locale=C.UTF-8 --encoding=UTF8 \
    -- --auth-local=peer --auth-host=scram-sha-256
fi

sudo python3 - <<'PY'
from pathlib import Path
path = Path("/etc/postgresql/16/releasegate/postgresql.conf")
lines = []
seen_listen = seen_port = False
for line in path.read_text().splitlines():
    if line.startswith("listen_addresses") or line.startswith("#listen_addresses"):
        lines.append("listen_addresses = '127.0.0.1'")
        seen_listen = True
    elif line.startswith("port ") or line.startswith("#port "):
        lines.append("port = 55432")
        seen_port = True
    else:
        lines.append(line)
if not seen_listen or not seen_port:
    raise SystemExit("postgresql.conf is missing listen_addresses or port")
path.write_text("\n".join(lines) + "\n")
PY

status=$(pg_lsclusters --no-header | awk '$1=="16" && $2=="releasegate" { print $4 }')
if [[ "$status" != "online" ]]; then
  sudo pg_ctlcluster 16 releasegate start
fi
pg_lsclusters
