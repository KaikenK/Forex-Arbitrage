#!/usr/bin/env sh
# Arbex semantic microservice: news NLP + reaction model + live feed.
# Default http://127.0.0.1:9000 (API also under /v1). Extra args go to serve.py,
# e.g.  ./run.sh --use-finbert --port 9100
set -eu
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; else PY="python3"; fi
[ -f config.toml ] || echo "[info] No config.toml - running on defaults. Copy config.toml.example to config.toml to add API keys."

exec "$PY" serve.py "$@"
