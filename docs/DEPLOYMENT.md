# Deployment, backups and upgrades

## Docker Compose

```bash
cp .env.example .env     # optional
docker compose up -d --build
docker compose logs -f chimera
```

- Data lives in the named volume `chimera-data` (mounted at `/data`).
- The container has a health check (`GET /api/health`).
- The port is bound to `127.0.0.1:8700`. For LAN access change the mapping to
  `"8700:8700"` and set `CHIMERA_ALLOWED_HOSTS` in `.env` (see
  [SECURITY.md](SECURITY.md); HTTPS is strongly recommended off-localhost).

## First run: create the administrator

Open the app. With no accounts yet, Chimera shows **Set up Chimera**: the first
account you create becomes the **administrator**. With the default registration
mode (`first_user_only`) self-registration then closes; add further people in
**Settings → Users** (or switch the mode to *Open*). If others can reach the app
before you finish setup, set `CHIMERA_SETUP_TOKEN` so only someone who knows it
can create the first account.

## Password recovery (no email needed)

- An administrator: **Settings → Users → ⋯ → Reset password** shows a one-time
  temporary password; the user must choose a new one at next sign-in.
- Lost the only admin password? On the server:

  ```bash
  # native
  backend/.venv/bin/python -m chimera.cli reset-password <login-or-email>
  # docker
  docker compose exec chimera python -m chimera.cli reset-password <login-or-email>
  ```

  Other commands: `list-users`, `create-user <email> <login> [--admin]`,
  `set-role <login> admin|user`, `enable|disable <login>`,
  `registration open|closed|first_user_only`.

## Running llama-server in Compose too

```bash
mkdir -p models && cp ~/Downloads/qwen2.5-7b-instruct-q4_k_m.gguf models/
LLAMA_MODEL=qwen2.5-7b-instruct-q4_k_m.gguf docker compose --profile llama up -d
```

Then add a connection to `http://llama-server:8080` in Chimera (it's not
published to the host). The default image is CPU-only; for NVIDIA GPUs use
`ghcr.io/ggml-org/llama.cpp:server-cuda` with
`deploy.resources.reservations.devices` (see the llama.cpp Docker docs).

## Web search with SearXNG in Compose

```bash
echo "SEARXNG_SECRET=$(openssl rand -hex 32)" >> .env
docker compose --profile searxng up -d
```

Then Settings → Web: allow web access, engine **SearXNG**, address
`http://searxng:8080`, **Test search**, **Save engine**. The SearXNG container
is not published to the host and refuses to start without a secret. Details,
API engines and the security model: [WEB.md](WEB.md).

## Native

```bash
./scripts/install.sh
./scripts/start.sh
```

`start.sh` reads `.env` from the repository root. Data goes to `./data` by
default (`CHIMERA_DATA_DIR`).

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

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now chimera
```

## Offline operation

Once installed (Docker image built, or `install.sh` run), Chimera needs no
internet access:

- The UI bundle includes all fonts, KaTeX fonts, icons and libraries.
- The backend has no external calls except to the connections you configure.
- The end-to-end suite verifies this by blocking every non-local request.

To install on an air-gapped machine, build the image elsewhere and transfer it:

```bash
docker save chimera:latest | gzip > chimera.tar.gz
# on the target
docker load < chimera.tar.gz
```

## Backups

Three options, from simplest:

1. **Settings → Privacy & data → Download backup** (administrators). A
   consistent SQLite copy (SQLite's online backup API) of everything: all
   accounts, conversations, connections (API keys stay encrypted), prompts,
   knowledge collections and settings. *Restore from backup* replaces the
   current data for everyone.
2. **Export JSON** (every user): your own conversations, prompts and settings
   in a portable format (no API keys, no knowledge index). *Import JSON* merges
   into your account.
3. **File-level**: stop Chimera and copy the whole data directory
   (`chimera.sqlite*`, `uploads/`, `backups/`, `.encryption_key`). Without
   `.encryption_key` (or `CHIMERA_ENCRYPTION_KEY`) stored API keys can't be
   decrypted. With Docker:

   ```bash
   docker compose stop chimera
   docker run --rm -v chimera_chimera-data:/data -v "$PWD":/backup alpine \
     tar czf /backup/chimera-data.tgz -C /data .
   docker compose start chimera
   ```

Image attachments live in `uploads/` and are not part of the SQLite backup;
include the directory for complete file-level backups.

## Upgrades

1. Back up (see above).
2. Update the code: `git pull`.
3. Docker: `docker compose up -d --build`. Native: `./scripts/install.sh` then restart.

Database migrations run automatically at startup. They are transactional (a
failed migration leaves the database unchanged), and before migrating a
populated database Chimera **writes a copy** to
`data/backups/pre-migration-v<old>-to-v<new>-<timestamp>.sqlite` and logs its
path.

### Upgrading from a version without accounts

Version 0.1 had no accounts (optionally a single shared password). After the
upgrade:

- `CHIMERA_PASSWORD` and `CHIMERA_ALLOW_UNAUTHENTICATED_REMOTE` are no longer
  used; remove them from `.env`/compose.
- Open the app and register: the **first account becomes the administrator and
  adopts all existing data** (conversations, prompts, settings, connections,
  attachments, knowledge collections). A one-time notice in the UI lists what
  was adopted, and the server log records it. Accounts created later start empty.
- Stored API keys are encrypted at rest on first start.

### Upgrading to 0.3 (web reading and search)

Migration 3 adds the `web_sources` table and a `messages.web` column (a backup
is written first, as for every migration). Web access stays **off** until an
administrator enables it; nothing else changes for users. The change is
additive, so 0.2 can still open a 0.3 database if you need to roll back (it
ignores the web data), but the backup copy is the clean way back. New Python
dependencies (`trafilatura`, `lxml`, `charset-normalizer`) are installed by
`./scripts/install.sh` or the Docker build.

### Rolling back a migration

Stop Chimera, then put the pre-migration copy back and run the previous
version:

```bash
cp data/backups/pre-migration-v1-to-v2-*.sqlite data/chimera.sqlite
rm -f data/chimera.sqlite-wal data/chimera.sqlite-shm
git checkout <previous-tag> && ./scripts/install.sh && ./scripts/start.sh
```

(Docker: do the same inside the `chimera-data` volume, e.g. with a temporary
`alpine` container as shown above.) Data created after the upgrade is lost by a
rollback.
