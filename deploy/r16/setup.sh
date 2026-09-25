#!/usr/bin/env bash
# One-time VPS bring-up for the R16 runner (Ubuntu/Debian). Idempotent.
# Run as root from a devtools checkout: sudo GIT_BASE=git@github.com:<org> deploy/r16/setup.sh
# Installs units but does NOT turn the timer on: switching executors is the
# handover in deploy/r16/README.md (spec §2.2), never a side effect of setup.
set -euo pipefail

R16_HOME=/srv/r16
UNIT_DIR=/etc/systemd/system
GIT_BASE="${GIT_BASE:?set GIT_BASE, e.g. git@github.com:your-org}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "== packages =="
apt-get update -q
apt-get install -y -q git tzdata python3
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
command -v gh >/dev/null || { echo ">>> install gh (https://cli.github.com) and re-run"; exit 1; }

echo "== user, group =="
getent group r16-readers >/dev/null || groupadd --system r16-readers
id r16 &>/dev/null || useradd --system --home-dir "$R16_HOME" --shell /usr/sbin/nologin r16
if id robin &>/dev/null; then usermod -aG r16-readers robin; fi

echo "== layout and permissions (spec §1.3) =="
install -d -o r16 -g r16-readers -m 0710 "$R16_HOME"
install -d -o r16 -g r16 -m 0700 "$R16_HOME/devtools" "$R16_HOME/workspace"
install -d -o r16 -g r16-readers -m 0710 "$R16_HOME/state"
install -d -o r16 -g r16-readers -m 2750 "$R16_HOME/state/receipts"
install -d -o r16 -g r16 -m 0700 "$R16_HOME/gh"
[ -f "$R16_HOME/state/r16.lock" ] || install -o r16 -g r16 -m 0600 /dev/null "$R16_HOME/state/r16.lock"

echo "== code (updated by hand later: pull --ff-only) =="
[ -d "$R16_HOME/devtools/.git" ] || sudo -u r16 git clone -q "$GIT_BASE/devtools.git" "$R16_HOME/devtools"

echo "== workspace clones: canonical names from workspace-manifest.toml =="
WS="$R16_HOME/workspace"
[ -d "$WS/ai-orchestrators-workspace/.git" ] || sudo -u r16 git clone -q "$GIT_BASE/ai-orchestrators-workspace.git" "$WS/ai-orchestrators-workspace"
mapfile -t REPOS < <(python3 - "$WS/ai-orchestrators-workspace/workspace-manifest.toml" <<'PY'
import sys, tomllib
m = tomllib.load(open(sys.argv[1], "rb"))
dirs = {
    v["git_dir"]
    for sec in m.values() if isinstance(sec, dict)
    for v in sec.values()
    if isinstance(v, dict) and "git_dir" in v and "repo_url" in v and not v.get("member")
}
print("\n".join(sorted(dirs)))
PY
)
for repo in "${REPOS[@]}"; do
    [ -d "$WS/$repo/.git" ] || sudo -u r16 git clone -q "$GIT_BASE/$repo.git" "$WS/$repo"
done

echo "== env =="
if [ ! -f "$R16_HOME/r16.env" ]; then
    install -o r16 -g r16 -m 0600 "$HERE/env.example" "$R16_HOME/r16.env"
    echo ">>> EDIT $R16_HOME/r16.env (R16_HOST_LABEL) before the handover"
fi

echo "== systemd units (installed, timer left off) =="
cp "$HERE/r16-kb-freshness.service" "$HERE/r16-kb-freshness.timer" "$UNIT_DIR"/
systemctl daemon-reload
echo ">>> gh profile: put ai-prosto's hosts.yml into $R16_HOME/gh (0600, r16:r16)"
echo ">>> then follow deploy/r16/README.md: dry-run, handover steps 2-4"
