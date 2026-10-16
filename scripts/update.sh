#!/usr/bin/env bash
# Update a Docker Compose installation of Chimera, with a backup and an
# automatic rollback if the new version doesn't become healthy.
#
#   ./update.sh                    # newest release in the same major line (asks before changing anything)
#   ./update.sh --version 1.2.3    # a specific release
#   ./update.sh --channel patch    # only 1.2.x updates (what automatic updates use)
#   ./update.sh --dry-run          # show what would happen
#
# Options:
#   --dir DIR             installation directory (default: this script's directory)
#   --version X.Y.Z       target release (default: resolved from --channel)
#   --channel minor|patch newest release in the same major (minor, default) or the same minor (patch)
#   --allow-major         allow moving to a new major version (read its release notes first)
#   --pre                 consider pre-releases (release candidates)
#   --timeout SECONDS     how long to wait for health (default 300)
#   --keep N              keep the newest N update backups (default 5)
#   --dry-run             resolve and print the plan, change nothing
#   --yes                 don't ask for confirmation
#
# What it does: pull the new images (nothing changes if that fails), back up
# .env, docker-compose.yml and the whole data volume (Chimera is stopped for a
# consistent copy), switch CHIMERA_VERSION in .env, start, and wait for the
# health check. If the new version doesn't become healthy, it puts the
# previous .env, compose file, data and images back and starts them again.
# Afterwards it removes only the images it replaced; volumes are never touched.
# Log: <dir>/update.log. Details: docs/DEPLOYMENT.md, "Updating".
set -euo pipefail

REPO="Riloative/chimera"
RELEASES_API="${CHIMERA_RELEASES_API:-https://api.github.com/repos/$REPO/releases?per_page=100}"
RELEASE_BASE="${CHIMERA_RELEASE_BASE:-https://github.com/$REPO/releases/download}"
IMAGES="ghcr.io/riloative/chimera ghcr.io/riloative/chimera-browser"

DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET=""
CHANNEL="minor"
ALLOW_MAJOR=0
PRE=0
TIMEOUT=300
KEEP=5
DRY=0
YES=0

die() { log "ERROR: $*"; exit 1; }
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "${LOG:-/dev/null}" >&2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="${2:?}"; shift 2 ;;
    --version) TARGET="${2:?}"; TARGET="${TARGET#v}"; shift 2 ;;
    --channel) CHANNEL="${2:?}"; shift 2 ;;
    --allow-major) ALLOW_MAJOR=1; shift ;;
    --pre) PRE=1; shift ;;
    --timeout) TIMEOUT="${2:?}"; shift 2 ;;
    --keep) KEEP="${2:?}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --yes|-y) YES=1; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done
case "$CHANNEL" in minor|patch) ;; *) echo "--channel must be minor or patch" >&2; exit 2 ;; esac

cd "$DIR"
LOG="$DIR/update.log"
[ -f docker-compose.yml ] || die "No docker-compose.yml in $DIR (use --dir)."
[ -f .env ] || die "No .env in $DIR."
command -v docker >/dev/null 2>&1 || die "Docker is not installed."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required."
command -v python3 >/dev/null 2>&1 || die "python3 is required to compare versions."

# One update at a time (a timer and a manual run must not overlap).
exec 9>"$DIR/.update.lock"
if command -v flock >/dev/null 2>&1; then flock -n 9 || die "Another update is running."; fi

env_get() { sed -n "s/^$1=//p" .env | tail -n1 | tr -d '"'"'"; }
env_set() { # env_set KEY VALUE: replace or append, keeping everything else
  local tmp
  tmp="$(mktemp "$DIR/.env.XXXXXX")"
  if grep -q "^$1=" .env; then sed "s|^$1=.*|$1=$2|" .env > "$tmp"; else cat .env > "$tmp"; printf '%s=%s\n' "$1" "$2" >> "$tmp"; fi
  chmod 600 "$tmp"
  mv "$tmp" .env
}
semver() { # semver cmp A B → -1/0/1 ; semver ok V ; semver major V ; semver minor V
  python3 - "$@" <<'PY'
import re, sys
P = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")
def key(v):
    m = P.match(v)
    if not m:
        raise SystemExit(f"not a version: {v}")
    pre = m.group(4)
    ids = [] if pre is None else [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")]
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if pre else 1, ids)
op = sys.argv[1]
if op == "ok":
    sys.exit(0 if P.match(sys.argv[2]) else 1)
if op == "cmp":
    a, b = key(sys.argv[2]), key(sys.argv[3])
    print((a > b) - (a < b))
if op in ("major", "minor"):
    m = P.match(sys.argv[2])
    print(m.group(1) if op == "major" else f"{m.group(1)}.{m.group(2)}")
PY
}

current_version() {
  local v
  v="$(env_get CHIMERA_VERSION)"
  # shellcheck disable=SC2016 # a literal ${CHIMERA_VERSION:-…} in the compose file
  if [ -z "$v" ]; then v="$(sed -n 's/.*ghcr\.io\/riloative\/chimera:\${CHIMERA_VERSION:-\([^}]*\)}.*/\1/p' docker-compose.yml | head -n1)"; fi
  printf '%s' "$v"
}
resolve_target() { # newest release allowed by channel/major/pre rules
  local releases
  releases="$(curl -fsSL --proto '=https,file' "$RELEASES_API")" || return 1
  RELEASES_JSON="$releases" python3 - "$CURRENT" "$CHANNEL" "$PRE" <<'PY'
import json, os, re, sys
current, channel, pre = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
P = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")
def key(v):
    m = P.match(v)
    p = m.group(4)
    ids = [] if p is None else [(0, int(x), "") if x.isdigit() else (1, 0, x) for x in p.split(".")]
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if p else 1, ids)
cm = P.match(current)
best = None
for rel in json.loads(os.environ["RELEASES_JSON"]):
    v = str(rel.get("tag_name", "")).removeprefix("v")
    m = P.match(v)
    if not m or rel.get("draft") or (rel.get("prerelease") and not pre) or (m.group(4) and not pre):
        continue
    if m.group(1) != cm.group(1):
        continue  # never across a major version here (use --version with --allow-major)
    if channel == "patch" and m.group(2) != cm.group(2):
        continue
    if key(v) <= key(current):
        continue
    if best is None or key(v) > key(best):
        best = v
print(best or "")
PY
}

container() { docker compose ps -q chimera 2>/dev/null || true; }
health() { # prints: <state>|<health>|<restart count>
  local cid
  cid="$(container)"
  [ -n "$cid" ] || { echo "stopped||0"; return; }
  docker inspect --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.RestartCount}}' "$cid" 2>/dev/null \
    || echo "unknown||0"
}
wait_healthy() {
  local waited=0 state="" status="" restarts=0
  while [ "$waited" -lt "$TIMEOUT" ]; do
    IFS='|' read -r state status restarts <<< "$(health)"
    [ "$status" = healthy ] && return 0
    [ -z "$status" ] && [ "$state" = running ] && return 0  # an image without a health check
    # Crashing on start (restart loop) or gone: no point in waiting longer.
    case "$state" in exited|dead|restarting) break ;; esac
    [ "${restarts:-0}" -ge 2 ] && break
    sleep 3
    waited=$((waited + 3))
  done
  log "Not healthy after ${waited}s (state: $state, health: ${status:-none}, restarts: ${restarts:-0})."
  return 1
}

# ------------------------------------------------------------ resolve
CURRENT="$(current_version)"
semver ok "$CURRENT" || die "Can't tell the installed version (set CHIMERA_VERSION in .env)."
if [ -z "$TARGET" ]; then
  TARGET="$(resolve_target)" || die "Could not list releases ($RELEASES_API)."
  if [ -z "$TARGET" ]; then
    log "Chimera $CURRENT is up to date (channel: $CHANNEL)."
    exit 0
  fi
fi
semver ok "$TARGET" || die "Not a version: $TARGET"
case "$(semver cmp "$TARGET" "$CURRENT")" in
  0) log "Chimera $CURRENT is already installed."; exit 0 ;;
  -1) die "$TARGET is older than the installed $CURRENT. Downgrades are not supported (the database may have been migrated); to go back, restore a backup (docs/DEPLOYMENT.md, \"Rolling back\")." ;;
esac
if [ "$(semver major "$TARGET")" != "$(semver major "$CURRENT")" ] && [ "$ALLOW_MAJOR" -ne 1 ]; then
  die "$CURRENT → $TARGET is a new major version, which may need manual steps. Read the release notes, then run with --allow-major."
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$DIR/backups/update-$STAMP-from-$CURRENT"
PROFILES="$(env_get COMPOSE_PROFILES)"
log "Plan: Chimera $CURRENT → $TARGET (profiles: ${PROFILES:-none}). Backup: $BACKUP_DIR"
if [ "$DRY" -eq 1 ]; then
  log "Dry run: would pull ghcr.io/riloative/chimera:$TARGET${PROFILES:+ (and the images of: $PROFILES)}, back up .env, docker-compose.yml and the data volume, switch CHIMERA_VERSION, restart, wait up to ${TIMEOUT}s for health, and roll back on failure."
  exit 0
fi
if [ "$YES" -ne 1 ]; then
  if [ -t 0 ]; then
    read -r -p "Update now? Chimera will be unavailable for about a minute. [y/N] " answer
    case "$answer" in [Yy]*) ;; *) log "Cancelled."; exit 0 ;; esac
  else
    die "Not confirmed: add --yes to run without a terminal."
  fi
fi

# --------------------------------------------- 1. new compose file + images
fetch() { curl -fsSL --proto '=https,file' --retry 3 -o "$2" "$1"; }
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
log "Downloading the release files of $TARGET."
fetch "$RELEASE_BASE/v$TARGET/SHA256SUMS" "$WORK/SHA256SUMS" || die "Could not download the checksums of $TARGET."
for asset in docker-compose.yml update.sh; do
  fetch "$RELEASE_BASE/v$TARGET/$asset" "$WORK/$asset" || die "Could not download $asset of $TARGET."
  grep -q "  $asset\$" "$WORK/SHA256SUMS" || die "$asset is not listed in the checksums."
done
(cd "$WORK" && sha256sum --quiet --check --ignore-missing SHA256SUMS) || die "Checksum mismatch: nothing was changed."

log "Pulling the images of $TARGET (Chimera keeps running meanwhile)."
CHIMERA_VERSION="$TARGET" docker compose -f "$WORK/docker-compose.yml" --project-directory "$DIR" pull \
  || die "Pulling the images failed: nothing was changed."

# ---------------------------------------------------------- 2. backup
mkdir -p "$BACKUP_DIR"
chmod 700 "$DIR/backups" "$BACKUP_DIR"
cp -p .env docker-compose.yml "$BACKUP_DIR/"
[ ! -f docker-compose.override.yml ] || cp -p docker-compose.override.yml "$BACKUP_DIR/"
if [ -n "$(container)" ] && docker compose exec -T chimera chimera version >/dev/null 2>&1; then
  # A consistent archive inside the volume too (data/backups), readable by `chimera restore`.
  docker compose exec -T chimera chimera backup "/data/backups/before-update-$STAMP.tar.gz" >/dev/null \
    && log "Archive in the data volume: backups/before-update-$STAMP.tar.gz"
fi
log "Stopping Chimera for a consistent copy of the data volume."
docker compose stop chimera >/dev/null
if ! docker compose run --rm --no-deps -T --entrypoint tar chimera czf - -C /data . > "$BACKUP_DIR/data-volume.tar.gz" \
   || [ ! -s "$BACKUP_DIR/data-volume.tar.gz" ]; then
  docker compose up -d >/dev/null || true
  die "Backing up the data volume failed; the current version was started again and nothing was changed."
fi
chmod 600 "$BACKUP_DIR"/*
log "Backup written: $BACKUP_DIR"

# ------------------------------------------------------ 3. switch + start
cp "$WORK/docker-compose.yml" docker-compose.yml
env_set CHIMERA_VERSION "$TARGET"
log "Starting Chimera $TARGET."
if docker compose up -d >/dev/null && wait_healthy; then
  log "Chimera $TARGET is healthy."
  # Replace this script last (atomic rename: the running copy keeps its inode).
  install -m 755 "$WORK/update.sh" "$DIR/.update.sh.new" && mv "$DIR/.update.sh.new" "$DIR/update.sh"
  for image in $IMAGES; do docker image rm "$image:$CURRENT" >/dev/null 2>&1 || true; done
  log "Removed the images of $CURRENT (volumes are never removed)."
  # Keep only the newest $KEEP update backups.
  find "$DIR/backups" -maxdepth 1 -type d -name 'update-*' | sort -r | tail -n +"$((KEEP + 1))" | while read -r old; do rm -rf "$old"; done
  log "Done: Chimera $CURRENT → $TARGET."
  exit 0
fi

# --------------------------------------------------------- 4. roll back
log "Chimera $TARGET did not become healthy; rolling back to $CURRENT."
docker compose logs --tail 50 chimera >> "$LOG" 2>&1 || true
docker compose stop chimera >/dev/null 2>&1 || true
cp -p "$BACKUP_DIR/.env" .env
cp -p "$BACKUP_DIR/docker-compose.yml" docker-compose.yml
docker compose run --rm --no-deps -T --entrypoint sh chimera -c 'find /data -mindepth 1 -delete && tar xzf - -C /data' \
  < "$BACKUP_DIR/data-volume.tar.gz" || die "Restoring the data volume failed. The backup is in $BACKUP_DIR (docs/DEPLOYMENT.md, \"Rolling back\")."
docker compose up -d >/dev/null
if wait_healthy; then
  log "Rolled back: Chimera $CURRENT is running again with its data from before the update. The failed update's log is in $LOG."
else
  log "Rolled back the files and data, but Chimera $CURRENT is not healthy either. See: docker compose logs chimera"
fi
exit 1
