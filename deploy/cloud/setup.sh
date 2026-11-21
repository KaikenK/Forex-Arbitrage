#!/usr/bin/env bash
# Install or update the Arbex recorder on a fresh Ubuntu 24.04 server.
#   sudo bash setup.sh            install everything and start both services
#   sudo bash setup.sh --no-start install only
# Layout: code /opt/arbex/app (git checkout, read-only for the services), Python /opt/arbex/venv,
# data /var/lib/arbex (recordings, shadow, runtime = prediction log, cache = calendar archive, hf = model).
# The services run as the unprivileged user "arbex" under systemd and restart on failure or reboot.
set -euo pipefail

REPO="${REPO:-https://github.com/KaikenK/Forex-Arbitrage.git}"
BRANCH="${BRANCH:-semantic-microservice}"
COMMIT="${COMMIT:-}"                 # pin an exact commit for a research sample
BASE=/opt/arbex
APP=$BASE/app
VENV=$BASE/venv
DATA=/var/lib/arbex
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "== packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y -q
apt-get install -y -q git curl ca-certificates

echo "== swap (small servers: FinBERT needs about 1 GB on top of the backend)"
mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$mem_mb" -lt 3000 ] && ! swapon --show | grep -q /swapfile; then
    fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo 'vm.swappiness=10' > /etc/sysctl.d/90-arbex-swap.conf && sysctl -q -p /etc/sysctl.d/90-arbex-swap.conf
    echo "   4 GB swap added (memory ${mem_mb} MB)"
fi

echo "== service user and folders"
id arbex >/dev/null 2>&1 || useradd --system --home-dir $BASE --shell /usr/sbin/nologin arbex
mkdir -p $BASE $DATA/recordings $DATA/shadow $DATA/runtime $DATA/cache $DATA/hf /etc/arbex
chmod 755 $DATA $DATA/*

echo "== code ($BRANCH${COMMIT:+ @ $COMMIT})"
git config --global --get-all safe.directory 2>/dev/null | grep -qx $APP || git config --global --add safe.directory $APP
if [ -d $APP/.git ]; then
    git -C $APP fetch -q origin "$BRANCH"
    git -C $APP checkout -q "${COMMIT:-origin/$BRANCH}"
else
    git clone -q --branch "$BRANCH" "$REPO" $APP
    [ -n "$COMMIT" ] && git -C $APP checkout -q "$COMMIT"
fi
git -C $APP log -1 --format='   at %h %s'

echo "== Python 3.14 (uv)"
if ! command -v uv >/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
fi
export UV_PYTHON_INSTALL_DIR=$BASE/python
uv python install 3.14
[ -x $VENV/bin/python ] || uv venv -q --python 3.14 $VENV
uv pip install -q --python $VENV/bin/python -r $HERE/requirements-cloud.txt
uv pip install -q --python $VENV/bin/python torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu

echo "== FinBERT model (downloaded once, then used offline)"
HF_HOME=$DATA/hf $VENV/bin/python - <<'PY'
from transformers import AutoModelForSequenceClassification, AutoTokenizer
AutoTokenizer.from_pretrained("ProsusAI/finbert")
AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert")
print("   FinBERT cached")
PY

echo "== configuration"
install -m 644 $HERE/semantic.env /etc/arbex/semantic.env
install -m 644 $HERE/backend.env /etc/arbex/backend.env
install -m 755 $HERE/start-semantic.sh $BASE/start-semantic.sh
install -m 755 $HERE/arbex-status.sh /usr/local/bin/arbex-status
install -m 644 $HERE/arbex-semantic.service /etc/systemd/system/arbex-semantic.service
install -m 644 $HERE/arbex-backend.service /etc/systemd/system/arbex-backend.service
mkdir -p /etc/systemd/journald.conf.d
install -m 644 $HERE/journald-arbex.conf /etc/systemd/journald.conf.d/arbex.conf
systemctl restart systemd-journald
if [ -f /etc/arbex/config.toml ]; then
    chown root:arbex /etc/arbex/config.toml && chmod 640 /etc/arbex/config.toml
    echo "   config.toml present (Finnhub on)"
else
    echo "   no /etc/arbex/config.toml: RSS feeds only (Finnhub off)"
fi

chown -R arbex:arbex $DATA
# The service keeps its own analysis history inside its folder, so the checkout belongs to arbex;
# root still updates it with git.
chown -R arbex:arbex $APP

systemctl daemon-reload
systemctl enable -q arbex-semantic arbex-backend
if [ "${1:-}" != "--no-start" ]; then
    echo "== starting"
    systemctl restart arbex-semantic
    systemctl restart arbex-backend
    sleep 5
    systemctl --no-pager --lines=0 status arbex-semantic arbex-backend || true
fi
echo "== done. Check with: arbex-status"
