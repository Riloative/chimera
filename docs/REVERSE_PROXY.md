# Reverse proxies and HTTPS

Chimera itself speaks plain HTTP and should stay on `127.0.0.1:8700`
(`CHIMERA_PUBLISH`, the default). For access from other machines put an HTTPS
reverse proxy or a tunnel in front. The examples use the placeholder
`chat.example.com`; replace it with your own name.

Whatever you use, set in `/opt/chimera/.env`:

```bash
# The names browsers use to reach Chimera (DNS-rebinding protection).
CHIMERA_ALLOWED_HOSTS=chat.example.com
# Where the proxy's requests come from, as seen INSIDE the container (see below).
CHIMERA_TRUSTED_PROXIES=172.30.0.1
# Only the first account may register (default), or "closed".
CHIMERA_REGISTRATION_MODE=first_user_only
```

and apply with `docker compose up -d`. `CHIMERA_COOKIE_SECURE=auto` (default)
marks the session cookie `Secure` as soon as a trusted proxy reports HTTPS.

**Which address to trust.** A proxy on the host connects to the published
port `127.0.0.1:8700`, but Docker forwards that connection into the container
from the Compose network's **gateway** address, not from 127.0.0.1. That
gateway is what `CHIMERA_TRUSTED_PROXIES` needs:

```bash
docker network inspect chimera_default --format '{{(index .IPAM.Config 0).Gateway}}'
```

The address can change when the network is recreated, so pin the subnet in
`docker-compose.override.yml` (then the gateway is always `172.30.0.1`):

```yaml
networks:
  default:
    ipam:
      config:
        - subnet: 172.30.0.0/24
```

If the proxy runs as a container on the same Compose network instead (and the
port isn't published), trust that container's address or the subnet. Never
trust a broader range than necessary: a trusted proxy can set the client IP
that rate limiting uses.

**Streaming.** Answers stream as Server-Sent Events (`text/event-stream`).
Chimera sends `X-Accel-Buffering: no` and a keep-alive comment every 15 s; the
proxy must not buffer responses and must allow long-lived requests (up to the
longest answer you expect, e.g. 10 minutes). WebSockets aren't used.

**Upload size.** Attachments are limited to 10 MB by default (Settings →
Documents, up to 100 MB); set the proxy's request body limit a little above
your setting (the examples allow 110 MB).

## Caddy

```caddyfile
chat.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8700 {
        # Stream answers as they are generated.
        flush_interval -1
        transport http {
            read_timeout 10m
        }
    }
    request_body {
        max_size 110MB
    }
}
```

Caddy obtains and renews the certificate itself and sends
`X-Forwarded-For`/`X-Forwarded-Proto`. Caddy on the host:
`CHIMERA_TRUSTED_PROXIES=<network gateway>` as above.

## nginx

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name chat.example.com;

    ssl_certificate     /etc/letsencrypt/live/chat.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/chat.example.com/privkey.pem;

    client_max_body_size 110m;

    location / {
        proxy_pass http://127.0.0.1:8700;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";

        # Server-Sent Events: no buffering, long reads.
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 10m;
        proxy_send_timeout 10m;
    }
}

server {
    listen 80;
    server_name chat.example.com;
    return 301 https://$host$request_uri;
}
```

## Cloudflare Tunnel

`cloudflared` connects outward to Cloudflare, so no port is opened on the
server. In the tunnel's public hostname settings (or `config.yml`):

```yaml
ingress:
  - hostname: chat.example.com
    service: http://127.0.0.1:8700
    originRequest:
      # Keep streams open; Cloudflare closes idle connections after 100 s,
      # which Chimera's 15 s keep-alive comments prevent.
      connectTimeout: 30s
  - service: http_status:404
```

- `cloudflared` on the host: it connects to the published port, so
  `CHIMERA_TRUSTED_PROXIES=<network gateway>` (above).
- `cloudflared` as a container on Chimera's Compose network: use
  `service: http://chimera:8700`, don't publish the port at all
  (`CHIMERA_PUBLISH` can't be empty, so remove the `ports:` mapping in an
  override), and trust the `cloudflared` container's address.
- Cloudflare passes the visitor's address in `CF-Connecting-IP` and
  `X-Forwarded-For`; Chimera uses `X-Forwarded-For` from trusted proxies.
- Point the tunnel at Chimera only. Never route a hostname to llama-server,
  SearXNG, the browser service or the browser egress proxy (8701).
- Consider Cloudflare Access in front for a second login factor.

## Checking the result

```bash
curl -sI https://chat.example.com/api/health      # 200, served over HTTPS
docker compose logs chimera | tail                # client IPs are the real visitors, not the gateway
```

In the browser, sign in and send a message: the answer should appear word by
word. If it arrives all at once, the proxy is buffering (see Streaming).
