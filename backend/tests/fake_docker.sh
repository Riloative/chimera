#!/usr/bin/env bash
# A stand-in for the `docker` command, for testing scripts/install-docker.sh and
# scripts/update.sh without Docker. State lives in $FAKE_STATE:
#   calls.log   every invocation
#   running     "1" while the chimera container runs
#   version     CHIMERA_VERSION of the running container (read from .env at `up`)
#   volume      contents of the data volume (a string)
# Knobs: FAKE_BAD_VERSION (that version never becomes healthy and "migrates"
# the volume), FAKE_PULL_FAIL=1, FAKE_NO_BACKUP_CLI=1 (an old image).
set -euo pipefail
S="${FAKE_STATE:?}"
echo "docker $*" >> "$S/calls.log"

env_version() { sed -n 's/^CHIMERA_VERSION=//p' .env 2>/dev/null | tail -n1; }

case "${1:-}" in
  info) exit 0 ;;
  version) echo "27.3.1"; exit 0 ;;
  inspect)
    # Answers both formats used: "<state>|<health>|<restarts>" (update.sh) and
    # "<health>|<restarts>" (install-docker.sh).
    fmt="${3:-}"
    if [ "$(cat "$S/running" 2>/dev/null)" != 1 ]; then state=exited; health=""; restarts=0
    elif [ -n "${FAKE_BAD_VERSION:-}" ] && [ "$(cat "$S/version")" = "$FAKE_BAD_VERSION" ]; then
      state="${FAKE_BAD_STATE:-running}"; health=unhealthy; restarts="${FAKE_BAD_RESTARTS:-0}"
    else state=running; health=healthy; restarts=0; fi
    case "$fmt" in
      "{{.State.Status}}"*) echo "$state|$health|$restarts" ;;
      *) echo "${health:-$state}|$restarts" ;;
    esac
    exit 0 ;;
  image) exit 0 ;;
  compose) shift ;;
  *) echo "fake docker: unsupported: $*" >&2; exit 2 ;;
esac

# docker compose [global options] <command> ...
while [ $# -gt 0 ]; do
  case "$1" in
    -f|-p|--project-directory|--profile) shift 2 ;;
    *) break ;;
  esac
done
cmd="${1:-}"; shift || true
case "$cmd" in
  version) echo "Docker Compose version v2.30.3"; [ "${1:-}" = "--short" ] && echo "2.30.3"; exit 0 ;;
  pull) [ "${FAKE_PULL_FAIL:-}" = 1 ] && { echo "pull failed" >&2; exit 1; }; exit 0 ;;
  up)
    v="$(env_version)"
    echo 1 > "$S/running"
    echo "$v" > "$S/version"
    if [ -n "${FAKE_BAD_VERSION:-}" ] && [ "$v" = "$FAKE_BAD_VERSION" ]; then
      echo "migrated-by-$v" > "$S/volume"   # a migration that ran before the failure
    fi
    exit 0 ;;
  stop) echo 0 > "$S/running"; exit 0 ;;
  ps) [ "$(cat "$S/running" 2>/dev/null)" = 1 ] && echo "c0ffee"; exit 0 ;;
  logs) echo "fake logs"; exit 0 ;;
  exec)
    # exec -T chimera chimera <version|backup PATH>
    [ "$(cat "$S/running" 2>/dev/null)" = 1 ] || exit 1
    [ "${FAKE_NO_BACKUP_CLI:-}" = 1 ] && exit 1
    exit 0 ;;
  run)
    args="$*"
    case "$args" in
      *"--entrypoint tar"*) printf 'VOLUME:%s' "$(cat "$S/volume")"; exit 0 ;;
      *"--entrypoint sh"*) data="$(cat)"; printf '%s\n' "${data#VOLUME:}" > "$S/volume"; exit 0 ;;
    esac
    exit 0 ;;
  *) echo "fake docker compose: unsupported: $cmd" >&2; exit 2 ;;
esac
