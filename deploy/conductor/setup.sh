#!/usr/bin/env bash
# One-time VPS bring-up for conductor slice 0 (level 0, read-only). Idempotent.
# Run as root from a devtools checkout:
#   sudo GIT_BASE=https://github.com/<owner> deploy/conductor/setup.sh
# Clones over https without a key (like deploy/r16) and with FULL history:
# blame, movement and deletion history are wrong on a shallow clone.
# Installs units but does NOT enable the timer: that is the owner's step in
# deploy/conductor/README.md.
set -euo pipefail

HOME_DIR=/srv/conductor
UNIT_DIR=/etc/systemd/system
GIT_BASE="${GIT_BASE:?set GIT_BASE, e.g. https://github.com/your-org}"
HERE="$(cd "$(dirname "$0")" && pwd)"

apt-get update -q
apt-get install -y -q git python3 util-linux
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
command -v gh >/dev/null || { echo ">>> install gh (https://cli.github.com) and re-run"; exit 1; }

id conductor &>/dev/null || useradd --system --home-dir "$HOME_DIR" --shell /usr/sbin/nologin conductor
install -d -o conductor -g conductor -m 0750 "$HOME_DIR"
install -d -o conductor -g conductor -m 0700 "$HOME_DIR/devtools" "$HOME_DIR/workspace" "$HOME_DIR/gh"
install -d -o conductor -g conductor -m 0750 "$HOME_DIR/state" "$HOME_DIR/state/runs"
[ -f "$HOME_DIR/state/conductor.lock" ] || install -o conductor -g conductor -m 0600 /dev/null "$HOME_DIR/state/conductor.lock"

[ -d "$HOME_DIR/devtools/.git" ] || sudo -u conductor git clone -q "$GIT_BASE/devtools.git" "$HOME_DIR/devtools"
WS="$HOME_DIR/workspace"
[ -d "$WS/ai-orchestrators-workspace/.git" ] || sudo -u conductor git clone -q "$GIT_BASE/ai-orchestrators-workspace.git" "$WS/ai-orchestrators-workspace"
sudo -u conductor env HOME="$HOME_DIR" python3 "$HOME_DIR/devtools/clone_fleet.py" \
    --manifest "$WS/ai-orchestrators-workspace/workspace-manifest.toml" --root "$WS" --https
for repo in "$WS"/*/; do
    if [ "$(sudo -u conductor git -C "$repo" rev-parse --is-shallow-repository)" = true ]; then
        sudo -u conductor git -C "$repo" fetch -q --unshallow
    fi
done

install -m 0644 "$HERE/conductor.service" "$HERE/conductor.timer" "$UNIT_DIR/"
systemctl daemon-reload
echo ">>> next: sudo -u conductor env GH_CONFIG_DIR=$HOME_DIR/gh gh auth login (read-only token), then README"
