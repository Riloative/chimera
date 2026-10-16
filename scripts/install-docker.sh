#!/usr/bin/env bash
# Install Chimera with Docker Compose from a published release.
#
#   curl -fsSLO https://github.com/Riloative/chimera/releases/latest/download/install-docker.sh
#   less install-docker.sh                 # read it first
#   sudo bash install-docker.sh            # interactive
#   sudo bash install-docker.sh --with searxng,browser --yes
#
# Options:
#   --dir DIR            install directory (default /opt/chimera)
#   --version X.Y.Z      release to install (default: the latest stable release)
#   --with LIST          optional profiles: searxng, browser, llama (comma-separated; "none" for none)
#   --publish ADDR:PORT  where Chimera listens on the host (default 127.0.0.1:8700)
#   --no-setup-token     don't require a setup token for the first (admin) account
#   --no-start           prepare everything but don't start
#   --yes                no questions (non-interactive)
#
# Idempotent: running it again never overwrites an existing .env or any data;
# docker-compose.yml is replaced by the release's (the previous one is kept as
# docker-compose.yml.bak-<time>); put your own changes in docker-compose.override.yml.
# The manual steps are in docs/DEPLOYMENT.md.
set -euo pipefail

REPO="Riloative/chimera"
RELEASE_BASE="${CHIMERA_RELEASE_BASE:-https://github.com/$REPO/releases/download}"
RELEASES_API="${CHIMERA_RELEASES_API:-https://api.github.com/repos/$REPO/releases/latest}"
MIN_FREE_MB="${CHIMERA_MIN_FREE_MB:-4096}"
HEALTH_TIMEOUT="${CHIMERA_HEALTH_TIMEOUT:-180}"

DIR="/opt/chimera"
VERSION=""
WITH=""
PUBLISH="127.0.0.1:8700"
SETUP_TOKEN=1
START=1
YES=0

say() { printf '%s\n' "$*"; }
step() { printf '\n→ %s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="${2:?--dir needs a value}"; shift 2 ;;
    --version) VERSION="${2:?--version needs a value}"; VERSION="${VERSION#v}"; shift 2 ;;
    --with) WITH="${2:?--with needs a value}"; shift 2 ;;
    --publish) PUBLISH="${2:?--publish needs a value}"; shift 2 ;;
    --no-setup-token) SETUP_TOKEN=0; shift ;;
    --no-start) START=0; shift ;;
    --yes|-y) YES=1; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done

interactive() { [ "$YES" -eq 0 ] && [ -t 0 ]; }
ask() { # ask "question" default(y/n)
  local answer
  if ! interactive; then [ "$2" = y ]; return; fi
  read -r -p "$1 [$( [ "$2" = y ] && echo Y/n || echo y/N )] " answer
  answer="${answer:-$2}"
  case "$answer" in [Yy]*) return 0 ;; *) return 1 ;; esac
}
random_hex() { # random_hex bytes
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex "$1"
  else od -An -tx1 -N"$1" /dev/urandom | tr -d ' \n'; fi
}
fetch() { # fetch URL FILE
  curl -fsSL --proto '=https,file' --retry 3 -o "$2" "$1"
}

# --------------------------------------------------------------- checks
step "Checking prerequisites"
command -v docker >/dev/null 2>&1 || die "Docker is not installed: https://docs.docker.com/engine/install/"
docker info >/dev/null 2>&1 || die "Docker is not running, or this user may not use it (try sudo)."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required (the 'docker compose' plugin)."
command -v curl >/dev/null 2>&1 || die "curl is required."
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required (coreutils)."
parent="$DIR"
while [ ! -d "$parent" ]; do parent="$(dirname "$parent")"; done
free_mb=$(( $(df -Pk "$parent" | awk 'NR==2 {print $4}') / 1024 ))
[ "$free_mb" -ge "$MIN_FREE_MB" ] || die "Only ${free_mb} MB free at $parent; at least ${MIN_FREE_MB} MB are needed (images, data, backups)."
say "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?'), $(docker compose version --short 2>/dev/null || echo 'compose v2'), ${free_mb} MB free."

if [ -z "$VERSION" ]; then
  VERSION="$(curl -fsSL --proto '=https,file' "$RELEASES_API" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"v\{0,1\}\([^"]*\)".*/\1/p' | head -n1)"
  [ -n "$VERSION" ] || die "Could not find the latest release. Pass --version X.Y.Z."
fi
case "$VERSION" in
  [0-9]*.[0-9]*.[0-9]*) ;;
  *) die "Not a version: $VERSION" ;;
esac
say "Installing Chimera $VERSION into $DIR."

# ------------------------------------------------------------- download
step "Downloading the release files and verifying checksums"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
ASSETS="docker-compose.yml .env.example searxng-settings.yml update.sh chimera-update.service chimera-update.timer"
fetch "$RELEASE_BASE/v$VERSION/SHA256SUMS" "$tmp/SHA256SUMS" || die "Could not download the checksums of v$VERSION."
for asset in $ASSETS; do
  fetch "$RELEASE_BASE/v$VERSION/$asset" "$tmp/$asset" || die "Could not download $asset."
  grep -q "  $asset\$" "$tmp/SHA256SUMS" || die "$asset is not listed in SHA256SUMS."
done
(cd "$tmp" && sha256sum --quiet --check --ignore-missing SHA256SUMS) || die "Checksum mismatch: refusing to install."
say "Checksums OK."

# ---------------------------------------------------------------- place
step "Preparing $DIR"
mkdir -p "$DIR" "$DIR/deploy/searxng" "$DIR/deploy/systemd" "$DIR/backups"
chmod 750 "$DIR"
chmod 700 "$DIR/backups"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
if [ -f "$DIR/docker-compose.yml" ] && ! cmp -s "$tmp/docker-compose.yml" "$DIR/docker-compose.yml"; then
  cp -p "$DIR/docker-compose.yml" "$DIR/docker-compose.yml.bak-$stamp"
  say "Kept the previous docker-compose.yml as docker-compose.yml.bak-$stamp."
fi
cp "$tmp/docker-compose.yml" "$DIR/docker-compose.yml"
cp "$tmp/.env.example" "$DIR/.env.example"
[ -f "$DIR/deploy/searxng/settings.yml" ] || cp "$tmp/searxng-settings.yml" "$DIR/deploy/searxng/settings.yml"
install -m 755 "$tmp/update.sh" "$DIR/update.sh"
cp "$tmp/chimera-update.service" "$tmp/chimera-update.timer" "$DIR/deploy/systemd/"

# ------------------------------------------------------------ .env
token=""
if [ -f "$DIR/.env" ]; then
  say ".env exists: leaving it unchanged (no secrets or settings are overwritten)."
  existing_profiles="$(sed -n 's/^COMPOSE_PROFILES=//p' "$DIR/.env" | tail -n1)"
  [ -z "$WITH" ] || [ "$WITH" = "$existing_profiles" ] || \
    say "Note: --with $WITH was not applied; edit COMPOSE_PROFILES in .env yourself."
else
  if [ -z "$WITH" ]; then
    WITH=""
    if ask "Enable web search with a private SearXNG (profile searxng)?" n; then WITH="searxng"; fi
    if ask "Enable the headless browser for JavaScript pages (profile browser, ~2 GB image)?" n; then WITH="${WITH:+$WITH,}browser"; fi
  fi
  [ "$WITH" = none ] && WITH=""
  for p in $(printf '%s' "$WITH" | tr ',' ' '); do
    case "$p" in searxng|browser|llama) ;; *) die "Unknown profile: $p (use searxng, browser, llama)";; esac
  done
  umask 077
  {
    printf '# Chimera settings for this installation (generated %s by install-docker.sh).\n' "$stamp"
    printf '# All options: .env.example. Never commit or share this file: it holds secrets.\n\n'
    printf 'CHIMERA_VERSION=%s\n' "$VERSION"
    printf 'COMPOSE_PROFILES=%s\n' "$WITH"
    printf 'CHIMERA_PUBLISH=%s\n' "$PUBLISH"
    if [ "$SETUP_TOKEN" -eq 1 ]; then
      token="$(random_hex 16)"
      printf '# Needed once, to create the first (administrator) account.\nCHIMERA_SETUP_TOKEN=%s\n' "$token"
    fi
    printf 'SEARXNG_SECRET=%s\n' "$(random_hex 32)"
    case ",$WITH," in
      *,browser,*) printf 'CHIMERA_BROWSER_URL=http://browser:3000\n' ;;
      *) printf '# Set when enabling the browser profile:\n# CHIMERA_BROWSER_URL=http://browser:3000\n' ;;
    esac
    printf 'CHIMERA_BROWSER_SECRET=%s\n' "$(random_hex 32)"
    case ",$WITH," in
      *,searxng,*) printf 'CHIMERA_WEB_SEARXNG_URL=http://searxng:8080\n' ;;
    esac
  } > "$DIR/.env"
  chmod 600 "$DIR/.env"
  umask 022
  say "Wrote $DIR/.env (mode 600) with fresh secrets."
fi

if [ "$START" -eq 0 ]; then
  say "Prepared. Start with: cd $DIR && docker compose up -d"
  exit 0
fi

# ---------------------------------------------------------------- start
step "Pulling images and starting"
cd "$DIR"
docker compose pull
docker compose up -d
say "Waiting for Chimera to become healthy (up to ${HEALTH_TIMEOUT}s)…"
waited=0
status=""
restarts=0
while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
  cid="$(docker compose ps -q chimera 2>/dev/null || true)"
  status="$( [ -n "$cid" ] && docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}|{{.RestartCount}}' "$cid" 2>/dev/null || echo "starting|0")"
  restarts="${status#*|}"
  status="${status%|*}"
  [ "${restarts:-0}" -ge 2 ] && break  # crashing on start
  [ "$status" = healthy ] && break
  sleep 3
  waited=$((waited + 3))
done
[ "$status" = healthy ] || die "Chimera did not become healthy (status: ${status:-unknown}). See: cd $DIR && docker compose logs chimera"

host="${PUBLISH%:*}"
port="${PUBLISH##*:}"
case "$host" in 0.0.0.0|"") host="<this-server>" ;; esac
say ""
say "Chimera $VERSION is running: http://$host:$port"
say ""
say "Next steps:"
say "  1. Open it and create the first account; it becomes the administrator."
if [ -n "$token" ]; then
  say "     Setup token (asked once, also in $DIR/.env): $token"
fi
say "  2. Connect llama-server: docs/DEPLOYMENT.md, \"llama-server on the same host\"."
say "  3. For access from other machines put HTTPS in front: docs/REVERSE_PROXY.md."
say "  4. Updates: $DIR/update.sh (backup + automatic rollback). Optional automatic"
say "     updates: docs/DEPLOYMENT.md, \"Automatic updates\"."
