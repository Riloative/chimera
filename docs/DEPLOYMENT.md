# Deployment, updates and backups

Chimera runs as a small set of containers from published, signed images
(`ghcr.io/riloative/chimera`, `ghcr.io/riloative/chimera-browser`; linux/amd64
and linux/arm64), or natively from a source checkout.

- [Install with Docker](#install-with-docker): the install script, or the manual steps
- [Profiles](#profiles): SearXNG, the headless browser, llama-server
- [llama-server on the same host](#llama-server-on-the-same-host)
- [Reverse proxies and HTTPS](#reverse-proxies-and-https)
- [Updating](#updating): manual, `update.sh`, [automatic updates](#automatic-updates), [rolling back](#rolling-back)
- [Moving a locally built 0.x install to the published images](#moving-a-locally-built-0x-install-to-the-published-images)
- [Backups](#backups), [Native install](#native-install), [Offline use](#offline-operation)

## Install with Docker

Requirements: Linux with Docker Engine 24+ and the Compose v2 plugin
(`docker compose version`), about 4 GB of free disk (6 GB with the browser
profile), 1 GB RAM for Chimera itself. Models run in llama-server (or another
OpenAI-compatible server), not in Chimera.

### With the install script

```bash
curl -fsSLO https://github.com/Riloative/chimera/releases/latest/download/install-docker.sh
less install-docker.sh                       # read what it does
sudo bash install-docker.sh                  # asks which profiles to enable
# or, non-interactively:
sudo bash install-docker.sh --with searxng,browser --yes
```

It checks Docker, Compose v2 and disk space; creates `/opt/chimera`
(`--dir` to change); downloads the release's `docker-compose.yml`,
`.env.example`, SearXNG settings, `update.sh` and the systemd units and
**verifies them against the release's `SHA256SUMS`**; writes a `.env` (mode
600) with fresh secrets (`SEARXNG_SECRET`, `CHIMERA_BROWSER_SECRET` and a
`CHIMERA_SETUP_TOKEN` for creating the first account); pulls the images,
starts the stack, waits for the health check and prints the address.

Running it again is safe: an existing `.env`, the SearXNG settings and all
data are never overwritten; a changed `docker-compose.yml` is replaced by the
release's and the old one kept as `docker-compose.yml.bak-<time>`. Put your
own changes in `docker-compose.override.yml`.

Options: `--version X.Y.Z`, `--with searxng,browser,llama|none`,
`--publish 127.0.0.1:8700`, `--no-setup-token`, `--no-start`, `--yes`.

### Manually

```bash
sudo mkdir -p /opt/chimera/deploy/searxng && cd /opt/chimera
V=1.0.0   # the release you want (https://github.com/Riloative/chimera/releases)
base=https://github.com/Riloative/chimera/releases/download/v$V
sudo curl -fsSLO "$base/docker-compose.yml" -O "$base/.env.example" -O "$base/update.sh" -O "$base/SHA256SUMS"
sudo curl -fsSL -o deploy/searxng/settings.yml "$base/searxng-settings.yml"
sudo sh -c 'cp deploy/searxng/settings.yml searxng-settings.yml && sha256sum --check --ignore-missing SHA256SUMS && rm searxng-settings.yml'
sudo chmod +x update.sh

sudo install -m 600 /dev/null .env
sudo tee -a .env > /dev/null <<EOF
CHIMERA_VERSION=$V
COMPOSE_PROFILES=
CHIMERA_SETUP_TOKEN=$(openssl rand -hex 16)
SEARXNG_SECRET=$(openssl rand -hex 32)
CHIMERA_BROWSER_SECRET=$(openssl rand -hex 32)
EOF
sudo docker compose up -d
sudo docker compose ps          # chimera should become "healthy"
```

Open <http://127.0.0.1:8700> on the server (or through an SSH tunnel:
`ssh -L 8700:127.0.0.1:8700 server`). Optionally verify the image signatures
first: [SECURITY.md → Verifying images](SECURITY.md#verifying-images).

### First run: create the administrator

With no accounts yet Chimera shows **Set up Chimera**: the first account
becomes the **administrator** (it asks for the setup token if
`CHIMERA_SETUP_TOKEN` is set, which the install script does). With the default
registration mode (`first_user_only`) self-registration then closes; add people
in **Settings → Users**. Chimera also asks you once whether it may check for
new versions (off unless you agree; see [Update notices](#update-notices-in-the-app)).

### Where things are

| | |
| --- | --- |
| `/opt/chimera/.env` | your settings and secrets (mode 600; never commit or share) |
| `/opt/chimera/docker-compose.yml` | the release's Compose file (don't edit; use an override) |
| `/opt/chimera/docker-compose.override.yml` | your own changes (optional) |
| volume `chimera_chimera-data` → `/data` | database, uploads, encryption key, in-volume backups |
| `/opt/chimera/backups/` | backups made by `update.sh` |
| `/opt/chimera/update.log` | what `update.sh` did |

`CHIMERA_PUBLISH` (default `127.0.0.1:8700`) is where Chimera listens on the
host. Keep it on 127.0.0.1 and put an HTTPS reverse proxy or tunnel in front
for access from elsewhere ([REVERSE_PROXY.md](REVERSE_PROXY.md)).

### Container hardening

The Chimera container runs as an unprivileged user with a read-only root
filesystem (only `/data` and a small `/tmp` are writable), no Linux
capabilities, `no-new-privileges`, memory (`CHIMERA_MEM_LIMIT`, default 2g)
and process limits, and a health check on `/api/ready` (migrations complete
and the database writable). The browser container is hardened the same way
and sits on an internal-only network. When the stack stops, running answers
get up to 10 s (`CHIMERA_SHUTDOWN_GRACE_SECONDS`) to finish and are then saved
as *interrupted*, never lost silently.

### Image pinning

`CHIMERA_VERSION` in `.env` pins Chimera's images to an exact release
(`1.0.0`). The Compose file's default is the release it came with. Floating
tags exist (`1.0`, `1`, `latest`) but aren't recommended: an update should be
a deliberate step with a backup (`update.sh`). Third-party images are pinned
to exact versions in `docker-compose.yml` (SearXNG `2026.10.15-b00212a83`,
llama.cpp `server-b11669`, Playwright `v1.56.0-noble` inside the browser
image); Dependabot proposes updates, which are tested in CI and shipped in
Chimera releases.

## Profiles

Enable optional services with `COMPOSE_PROFILES` in `.env` (comma-separated)
and `docker compose up -d`:

| Profile | Service | Needs in `.env` |
| --- | --- | --- |
| `searxng` | private metasearch for web search | `SEARXNG_SECRET`; then Settings → Web → engine SearXNG, `http://searxng:8080` |
| `browser` | headless Chromium for JavaScript pages | `CHIMERA_BROWSER_URL=http://browser:3000`, `CHIMERA_BROWSER_SECRET` |
| `llama` | llama-server in a container | `LLAMA_MODEL` (a GGUF in `./models`), optionally `LLAMA_CTX`, `LLAMA_PARALLEL` |

None of them publishes a port; only Chimera talks to them on Compose networks.
Details: [WEB.md](WEB.md) (SearXNG, browser), [LLAMA_SERVER.md](LLAMA_SERVER.md).

## llama-server on the same host

When Chimera runs in Docker and llama-server runs directly on the same Linux
host, Chimera can't use `127.0.0.1` (that's the container itself). Three
options, from simplest to most locked-down:

**A. llama-server in the `llama` profile.** Nothing listens on the host at all:
`COMPOSE_PROFILES=llama`, model in `/opt/chimera/models`, connection URL
`http://llama-server:8080`. Best when you don't need llama-server for anything else.

**B. llama-server on the Docker bridge address.** Pin Chimera's network so the
address is predictable, in `docker-compose.override.yml`:

```yaml
networks:
  default:
    ipam:
      config:
        - subnet: 172.30.0.0/24   # gateway 172.30.0.1 = the host
```

Start llama-server with `--host 172.30.0.1 --port 8080` (after Docker has
created the network; or `--host 0.0.0.0` and rely on the firewall) and set the
connection URL (or `CHIMERA_SEED_PROVIDER_URL`) to `http://172.30.0.1:8080`.
With UFW, allow exactly that path, **before** any deny rule (UFW applies the
first matching rule; `ufw status numbered` shows the order):

```bash
sudo ufw insert 1 allow proto tcp from 172.30.0.0/24 to 172.30.0.1 port 8080 comment 'Chimera to llama-server'
```

**C. llama-server stays on loopback, a socket proxy bridges.** llama-server
keeps `--host 127.0.0.1`; systemd forwards only the Docker network's gateway
address to it. With the pinned subnet from B:

```ini
# /etc/systemd/system/llama-bridge.socket
[Socket]
ListenStream=172.30.0.1:8080
# The bridge interface may not exist yet when this starts at boot:
FreeBind=true

[Install]
WantedBy=sockets.target
```

```ini
# /etc/systemd/system/llama-bridge.service
[Unit]
Requires=llama-bridge.socket
After=llama-bridge.socket

[Service]
ExecStart=/usr/lib/systemd/systemd-socket-proxyd 127.0.0.1:8080
PrivateTmp=yes
DynamicUser=yes
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now llama-bridge.socket
sudo ufw insert 1 allow proto tcp from 172.30.0.0/24 to 172.30.0.1 port 8080 comment 'Chimera to llama-server'
```

Connection URL: `http://172.30.0.1:8080`.

`host.docker.internal` (already mapped in the Compose file) points to the
default Docker bridge (`docker0`, usually 172.17.0.1), not to Chimera's own
network; it works with option B if llama-server listens there, but pinning the
network as above makes firewall rules exact.

`network_mode: host` would let Chimera reach `127.0.0.1:8080` directly, but it
is **not compatible with the browser profile**, which relies on Compose
networks (the internal `browser-net` and the `chimera` service name), and it
exposes everything Chimera listens on (including the browser egress proxy) on
the host. Not recommended.

Never publish llama-server to the internet; if it must listen on a network,
start it with `--api-key` and enter the key in the connection.

## Reverse proxies and HTTPS

See [REVERSE_PROXY.md](REVERSE_PROXY.md) for Caddy, nginx and Cloudflare
Tunnel. In short: keep `CHIMERA_PUBLISH=127.0.0.1:8700`, set
`CHIMERA_ALLOWED_HOSTS=chat.example.com`, and set `CHIMERA_TRUSTED_PROXIES` to
the address the proxy's requests arrive **from inside the container**. Through
the published port that is the Docker network's gateway, not 127.0.0.1:

```bash
docker network inspect chimera_default --format '{{(index .IPAM.Config 0).Gateway}}'   # e.g. 172.30.0.1
```

## Updating

Releases follow semantic versioning: patch (1.0.x) and minor (1.x) updates
need no manual steps; a new major version (2.0) may, and is never applied
without you asking for it. Each release's notes (GitHub Releases,
`CHANGELOG.md`) list database migrations, new settings and breaking changes.

### With update.sh (recommended)

```bash
cd /opt/chimera
sudo ./update.sh --dry-run        # what would happen
sudo ./update.sh                  # newest release in your major line; asks first
sudo ./update.sh --version 1.2.3  # a specific release
sudo ./update.sh --channel patch  # only 1.2.x
```

It downloads the new release's files (checksums verified) and **pulls the new
images while Chimera keeps running** (if that fails, nothing changes); writes
an archive with `chimera backup` inside the volume; stops Chimera and copies
the whole data volume plus `.env` and the Compose files to
`backups/update-<time>-from-<version>/`; switches `CHIMERA_VERSION`; starts
the new version and waits for its health check (`--timeout`, default 300 s;
a container that keeps crashing is detected within seconds). If the new
version doesn't become healthy, it **rolls back automatically**: previous
`.env`, Compose file, data volume and images, then checks that the old
version is healthy again. On success it removes only the images it replaced
(never volumes) and keeps the newest 5 update backups (`--keep`). Everything
is logged to `update.log`. Requirements on the host: `bash`, `curl`,
`sha256sum`, `python3` (to compare versions).

Going to a new major version needs `--allow-major` after reading its notes.
Downgrades are refused: to go back, [roll back](#rolling-back) to a backup.

### By hand

```bash
cd /opt/chimera
# 1. read the release notes; 2. back up:
sudo docker compose exec chimera chimera backup /data/backups/
# 3. pin the new version:
sudo sed -i 's/^CHIMERA_VERSION=.*/CHIMERA_VERSION=1.0.1/' .env
# 4. update the Compose file if the release changed it:
sudo curl -fsSLo docker-compose.yml https://github.com/Riloative/chimera/releases/download/v1.0.1/docker-compose.yml
# 5. pull and restart:
sudo docker compose pull && sudo docker compose up -d
# 6. verify:
sudo docker compose ps && curl -s http://127.0.0.1:8700/api/health
```

If it isn't healthy: set the previous `CHIMERA_VERSION` back and
`docker compose up -d`; if the new version had already migrated the database,
also restore the backup (below).

### Automatic updates

Off by default. To opt in, use the systemd timer that ships with each release
(installed into `/opt/chimera/deploy/systemd/` by the install script). It runs
`update.sh --channel patch --yes` once a day between 04:00 and 05:00, with the
same backup, health check and automatic rollback. It never crosses a major
version.

```bash
sudo cp /opt/chimera/deploy/systemd/chimera-update.{service,timer} /etc/systemd/system/
# optional settings:
printf 'CHIMERA_DIR=/opt/chimera\nCHIMERA_UPDATE_CHANNEL=patch\n' | sudo tee /etc/default/chimera-update
sudo systemctl daemon-reload && sudo systemctl enable --now chimera-update.timer
systemctl list-timers chimera-update.timer          # next run
journalctl -u chimera-update.service                 # what happened (also /opt/chimera/update.log)
```

- `CHIMERA_UPDATE_CHANNEL=patch` (default): only 1.0.x updates.
  `minor`: any 1.x. Pinned: disable the timer
  (`systemctl disable --now chimera-update.timer`) and set `CHIMERA_VERSION`.
- Time window: `sudo systemctl edit chimera-update.timer` and set a new
  `OnCalendar=` (e.g. `Sun *-*-* 03:00:00`).
- A failed, rolled-back update makes the unit fail (`systemctl status
  chimera-update`), so monitoring that watches failed units notices it.

Cron instead of systemd:

```cron
# /etc/cron.d/chimera-update
17 4 * * * root /opt/chimera/update.sh --dir /opt/chimera --channel patch --yes >/dev/null 2>&1
```

Tools like Watchtower that replace containers whenever a tag changes are **not
recommended**: they skip the backup before a database migration, don't wait
for the health check, can't roll back data that a migration already changed,
and would follow floating tags across minor or major versions.

### Update notices in the app

Administrators can turn on **Settings → About → Check for updates** (Chimera
asks once after setup; off by default). The server then asks the GitHub
Releases API at most once a day which releases exist, with a generic
User-Agent and nothing about you, your users or the server, and shows
administrators a quiet "Chimera 1.1.0 is available" with links to the release
notes and these instructions. Regular users see nothing. Nothing is ever
installed from the app. Offline or on failure nothing is shown to users; the
last result is in Settings → About. Mirrors: `CHIMERA_UPDATE_CHECK_URL`.

### Database migrations and the downgrade guard

Migrations run automatically when a new version starts. Each runs in one
transaction (a failure leaves the database unchanged), and before migrating a
populated database Chimera writes `backups/pre-migration-v<old>-to-v<new>-<time>.sqlite`
in the data directory.

An older Chimera never opens a database that a newer one migrated: it
**refuses to start** (exit code 3) and logs which versions are involved and
how to proceed, so nothing is damaged. Either run the newer version again, or
restore a backup from before the upgrade (next section).

### Rolling back

- **After a failed `update.sh`:** nothing to do; it rolled back.
- **After a successful update you want to undo:**

  ```bash
  cd /opt/chimera
  ls backups/                                       # update-<time>-from-<old version>/
  B=backups/update-20261016T040000Z-from-1.0.0
  sudo docker compose stop chimera
  sudo cp -p "$B/.env" "$B/docker-compose.yml" .
  sudo docker compose run --rm --no-deps -T --entrypoint sh chimera \
       -c 'find /data -mindepth 1 -delete && tar xzf - -C /data' < "$B/data-volume.tar.gz"
  sudo docker compose up -d
  ```

  Data created after that backup is lost.
- **From a `chimera backup` archive** (also works across machines):

  ```bash
  sudo docker compose stop chimera
  sudo docker compose run --rm --no-deps chimera chimera restore /data/backups/<archive>.tar.gz --yes
  sudo docker compose up -d
  ```

## Moving a locally built 0.x install to the published images

Installations of 0.x were usually a `git clone` (e.g. `/opt/chimera`) started
with `docker compose up -d --build`, sometimes with a
`docker-compose.override.yml` (pinned networks, extra security options…).
From 1.0, `docker-compose.yml` pulls published images instead of building.
Your data volume (`<folder>_chimera-data`), `.env` and override keep working.

1. **Back up** (0.x has no `chimera backup` yet), as root in the install folder:

   ```bash
   cd /opt/chimera
   docker compose stop chimera
   docker compose run --rm --no-deps -T --entrypoint tar chimera czf - -C /data . > /root/chimera-data-0.x.tar.gz
   cp -p .env /root/chimera-env-0.x
   ```

2. **Get 1.0** (keeps your `.env` and override):

   ```bash
   git fetch && git checkout v1.0.0     # or: git pull
   ```

3. **Check your override.** Remove from `docker-compose.override.yml` anything
   that builds or names images for `chimera`/`browser` (`build:`,
   `image: chimera:latest`, `image: chimera-browser:latest`); network pinning,
   `security_opt`, resource limits, extra volumes and port mappings stay.
   Check the merged result: `docker compose config | less`.

4. **Pin the version and profiles in `.env`** (`update.sh` and `docker compose
   up -d` then start the right services without flags):

   ```bash
   echo "CHIMERA_VERSION=1.0.0" >> .env
   echo "COMPOSE_PROFILES=searxng" >> .env      # the profiles you used, e.g. searxng,browser
   ```

5. **Pull and start**, then verify:

   ```bash
   docker compose pull && docker compose up -d
   docker compose ps                            # chimera: healthy
   curl -s http://127.0.0.1:8700/api/health     # {"status":"ok","version":"1.0.0"}
   ```

   0.4.x → 1.0 has no database migration; from 0.3 the usual pre-migration
   copy is written first. Old local images can be removed afterwards:
   `docker image rm chimera:latest chimera-browser:latest`.

6. **From now on** update with `./scripts/update.sh --dir /opt/chimera` (or
   copy `scripts/update.sh` next to `docker-compose.yml`). It replaces
   `docker-compose.yml` with the release's copy, which in a git checkout shows
   as a local change; that's expected (or keep updating with `git pull` and
   `docker compose pull && docker compose up -d`, after a backup).

Contributors who want to keep building locally use the development file:
`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build`.

## Backups

| Method | What | When |
| --- | --- | --- |
| `chimera backup` | consistent archive: database (SQLite online backup), uploads, key files, manifest | any time, while running |
| `update.sh` | the above plus a copy of the whole volume, `.env`, Compose files | automatically before each update |
| Settings → Privacy & data → Download backup | the database only (no uploads) | admins, from the browser |
| Export JSON | one user's conversations, prompts, settings | every user |

```bash
# write an archive inside the volume, then copy it off the server
sudo docker compose exec chimera chimera backup /data/backups/
sudo docker compose cp chimera:/data/backups/. ./backups-from-volume/
# restore (Chimera must be stopped; the current data is moved aside first)
sudo docker compose stop chimera
sudo docker compose run --rm --no-deps chimera chimera restore /data/backups/chimera-backup-<time>.tar.gz --yes
sudo docker compose up -d
```

Archives are created with mode 600 because they contain the encryption key
(without it, stored API keys can't be decrypted). `restore` refuses while the
server runs, checks the archive (paths, database integrity, schema version),
keeps the replaced data in `backups/pre-restore-<time>/` and migrates an older
backup to the running version (with the usual pre-migration copy). Keep
copies off the machine (e.g. `restic`/`borg` over `/opt/chimera/backups` and
the exported archives).

## Password recovery (no email needed)

- An administrator: **Settings → Users → ⋯ → Reset password** shows a one-time
  temporary password; the user chooses a new one at next sign-in.
- Lost the only admin password? On the server:

  ```bash
  sudo docker compose exec chimera chimera reset-password <login-or-email>
  ```

  Other commands: `list-users`, `create-user <email> <login> [--admin]`,
  `set-role <login> admin|user`, `enable|disable <login>`,
  `registration open|closed|first_user_only`, `version`, `backup`, `restore`.
  Native: `backend/.venv/bin/chimera <command>`.

## Native install

Without Docker (Python 3.11+, Node.js 22):

```bash
git clone https://github.com/Riloative/chimera && cd chimera
git checkout v1.0.0
./scripts/install.sh
./scripts/start.sh            # reads .env from the repository root; data in ./data
```

Update: back up (`backend/.venv/bin/chimera backup data/backups/`),
`git fetch && git checkout v<new>`, `./scripts/install.sh`, restart.
`chimera --check-config` validates the configuration without starting.

### systemd (Linux)

```ini
# ~/.config/systemd/user/chimera.service
[Unit]
Description=Chimera
After=network.target

[Service]
WorkingDirectory=%h/chimera
ExecStart=%h/chimera/scripts/start.sh
Restart=on-failure
# Lets running answers finish, then saves them as interrupted.
TimeoutStopSec=30

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now chimera
```

## Offline operation

Once installed, Chimera needs no internet access: the UI bundle includes all
fonts and libraries, and the server makes no requests except to the
connections you configure, web features you enable, and the update check if
an administrator turned it on. To install on an air-gapped machine:

```bash
# on a connected machine
docker pull ghcr.io/riloative/chimera:1.0.0
docker save ghcr.io/riloative/chimera:1.0.0 | gzip > chimera-1.0.0.tar.gz
# on the target, next to docker-compose.yml and .env (CHIMERA_VERSION=1.0.0)
docker load < chimera-1.0.0.tar.gz && docker compose up -d
```

## Release-specific upgrade notes

The full list is in [CHANGELOG.md](../CHANGELOG.md). Older highlights:

- **To 0.2 (accounts).** `CHIMERA_PASSWORD` and
  `CHIMERA_ALLOW_UNAUTHENTICATED_REMOTE` are no longer used. The first account
  registered becomes the administrator and adopts all existing data.
- **To 0.3 (web).** Migration 3 adds `web_sources`; web access stays off until
  an administrator enables it.
- **To 0.4 (branches, context, browser).** Migration 4 turns linear histories
  into message trees. 0.3 can't use a 0.4 database; roll back with the
  pre-migration copy.
