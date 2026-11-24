#!/usr/bin/env bash
# Isolated KnowLoop browser journey. Uses a disposable *_test database and a
# local model stub. Does not call a paid provider or touch another database.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/.local/bin:${PATH}"
LOG_DIR="${ROOT}/.local/ux-acceptance/logs"
mkdir -p "${LOG_DIR}" "${ROOT}/.local/ux-acceptance/storage"
ENV_FILE="${ROOT}/.env"
DB_NAME="knowloop_ux_acceptance_test"
DB_USER="knowloop_ux"
MOCK_PORT="${UX_MOCK_PORT:-18080}"

if [[ -f "${ENV_FILE}" ]] && ! grep -q 'KNOWLOOP_UX_ACCEPTANCE' "${ENV_FILE}"; then
  echo "Refusing to overwrite ${ENV_FILE}: it is not this isolated UX env." >&2
  exit 2
fi

sudo pg_ctlcluster 16 main start >/dev/null 2>&1 || true
sudo pg_isready -q

if [[ ! -f "${ENV_FILE}" ]]; then
  PW="$(openssl rand -hex 24)"
  JWT="$(openssl rand -hex 32)"
  FERNET="$(python3 -c 'import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN CREATE ROLE ${DB_USER} LOGIN SUPERUSER PASSWORD '${PW}'; ELSE ALTER ROLE ${DB_USER} WITH LOGIN SUPERUSER PASSWORD '${PW}'; END IF; END \$\$;" >/dev/null
  sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'" | grep -q 1 || sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"
  umask 077
  cat > "${ENV_FILE}" <<EOF
# KNOWLOOP_UX_ACCEPTANCE isolated disposable database. Do not commit.
DATABASE_URL=postgresql+psycopg://${DB_USER}:${PW}@127.0.0.1:5432/${DB_NAME}
MIGRATION_DATABASE_URL=postgresql+psycopg://${DB_USER}:${PW}@127.0.0.1:5432/${DB_NAME}
GRAPH_DATABASE_URL=postgresql://${DB_USER}:${PW}@127.0.0.1:5432/${DB_NAME}
TEST_DATABASE_URL=postgresql+psycopg://${DB_USER}:${PW}@127.0.0.1:5432/${DB_NAME}
AUTH_JWT_SECRET=${JWT}
AUTH_PUBLIC_URL=http://127.0.0.1:5173
COOKIE_SECURE=false
LEARNING_STUB_GRADER=true
DEEPSEEK_API_KEY=
EMBEDDING_API_KEY=
CHAT_FALLBACK_API_KEY=
RAG_QUERY_REWRITE_ENABLED=false
RETRIEVAL_LEXICAL_WEIGHT=0
MODEL_CONNECTIONS_KEY=${FERNET}
MODEL_ALLOWED_ORIGINS=["http://127.0.0.1:${MOCK_PORT}"]
STORAGE_DIR=${ROOT}/.local/ux-acceptance/storage
EOF
fi

sudo -u postgres psql -d "${DB_NAME}" -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS citext; CREATE SCHEMA IF NOT EXISTS study_graph;" >/dev/null

cd "${ROOT}/backend"
uv sync --locked
uv run alembic upgrade head
uv run study-setup-checkpointer
cd "${ROOT}"

if [[ ! -d frontend/node_modules ]]; then
  npm ci --prefix frontend
fi
if [[ ! -d frontend/e2e/node_modules ]]; then
  npm install --prefix frontend/e2e
fi
npm --prefix frontend/e2e exec -- playwright install chromium
sudo env "PATH=${PATH}" npx --yes playwright install-deps chromium

start_tmux() {
  local name="$1"
  shift
  tmux -f /exec-daemon/tmux.portal.conf has-session -t "=${name}" 2>/dev/null && tmux -f /exec-daemon/tmux.portal.conf kill-session -t "=${name}"
  tmux -f /exec-daemon/tmux.portal.conf new-session -d -s "${name}" -- env "PATH=${HOME}/.local/bin:${PATH}" bash -c "$*"
}

start_tmux knowloop-ux-mock "cd '${ROOT}' && UX_MOCK_PORT=${MOCK_PORT} UX_MOCK_LOG='${LOG_DIR}/mock.log' node frontend/e2e/support/mock-model.mjs"
start_tmux knowloop-ux-api "cd '${ROOT}/backend' && uv run study-api > '${LOG_DIR}/api.log' 2>&1"
start_tmux knowloop-ux-worker "cd '${ROOT}/backend' && uv run study-worker > '${LOG_DIR}/worker.log' 2>&1"
start_tmux knowloop-ux-web "cd '${ROOT}/frontend' && npm run dev -- --host 127.0.0.1 --port 5173 --strictPort > '${LOG_DIR}/web.log' 2>&1"

wait_http() {
  local url="$1"
  local i
  for i in $(seq 1 60); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for ${url}" >&2
  return 1
}

wait_http "http://127.0.0.1:${MOCK_PORT}/health"
wait_http "http://127.0.0.1:8000/health/ready"
wait_http "http://127.0.0.1:5173/"

{
  echo "sha $(git rev-parse HEAD)"
  echo "node $(node -v)"
  echo "python $(python3 --version)"
  echo "uv $(uv --version)"
  echo "postgres $(sudo -u postgres psql -tAc 'SHOW server_version')"
  echo "pgvector $(sudo -u postgres psql -d "${DB_NAME}" -tAc "SELECT extversion FROM pg_extension WHERE extname = 'vector'")"
  echo "playwright $(npm --prefix frontend/e2e exec -- playwright --version)"
  echo "database ${DB_NAME}"
  echo "mock http://127.0.0.1:${MOCK_PORT}/v1"
  echo "stub_grader true"
  echo "paid_provider false"
} | tee "${ROOT}/.local/ux-acceptance/environment.txt"

cd "${ROOT}/frontend/e2e"
npm test
