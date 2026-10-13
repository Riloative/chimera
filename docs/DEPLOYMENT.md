# Deployment, backups and upgrades

## Docker Compose

```bash
cp .env.example .env     # optional
docker compose up -d --build
docker compose logs -f chimera
```

- Data lives in the named volume `chimera-data` (mounted at `/data`).
- The container has a health check (`GET /api/health`).
- The port is bound to `127.0.0.1:8700`. For LAN access set `CHIMERA_PASSWORD`
  in `.env` and change the mapping to `"8700:8700"`.

### Running llama-server in Compose too

```bash
mkdir -p models && cp ~/Downloads/qwen2.5-7b-instruct-q4_k_m.gguf models/
LLAMA_MODEL=qwen2.5-7b-instruct-q4_k_m.gguf docker compose --profile llama up -d
```

Then add a connection to `http://llama-server:8080` in Chimera (it's not
published to the host). The default image is CPU-only; for NVIDIA GPUs use
`ghcr.io/ggml-org/llama.cpp:server-cuda` with
`deploy.resources.reservations.devices` (see the llama.cpp Docker docs).

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

1. **Settings → Privacy & data → Download backup.** A consistent SQLite copy
   (made with SQLite's online backup API) of everything: conversations,
   connections (including API keys), prompts, knowledge collections and
   settings. *Restore from backup* replaces the current data.
2. **Export JSON**: conversations, prompts and settings in a portable format
   (no API keys, no knowledge index). *Import JSON* merges into existing data.
3. **File-level**: stop Chimera and copy the data directory
   (`chimera.sqlite*`, `uploads/`, `.secret_key`). With Docker:

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

Database migrations run automatically at startup. They are forward-only and
transactional: a failed migration leaves the database unchanged.
