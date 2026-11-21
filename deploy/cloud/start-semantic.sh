#!/usr/bin/env bash
# Starts the semantic service; uses /etc/arbex/config.toml (API keys) only when it exists.
set -euo pipefail
[ -f /etc/arbex/config.toml ] && export SEMANTIC_CONFIG=/etc/arbex/config.toml
cd /opt/arbex/app/services/semantic
exec /opt/arbex/venv/bin/python serve.py
