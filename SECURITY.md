# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report them privately through GitHub: **Security → Report a vulnerability**
on <https://github.com/Riloative/chimera/security/advisories/new> (GitHub
private vulnerability reporting). Include what you found, how to reproduce it,
the affected version (Settings → About, or `GET /api/health`) and your
deployment (Docker or native, reverse proxy, profiles).

You can expect an acknowledgement within 7 days and an assessment within 14.
Fixes are released as a patch version with a GitHub security advisory; you are
credited unless you prefer otherwise. Please give us a reasonable time to ship
a fix before disclosing publicly (90 days at most).

## Supported versions

| Version | Supported |
| --- | --- |
| Latest 1.x minor (e.g. 1.2.x when 1.2 is current) | Security and bug fixes |
| Previous 1.x minor | Security fixes for 3 months after the next minor |
| 0.x | Not supported: upgrade to 1.x (docs/DEPLOYMENT.md) |

Container images are rebuilt for every release, which also picks up security
fixes of the base images; a release is blocked if a fixable critical
vulnerability is found in its images.

## Scope

In scope: the Chimera server and web UI, the browser service, the Compose
files, the install and update scripts, and the published images. The security
model, hardening and a production checklist are in
[docs/SECURITY.md](docs/SECURITY.md). Model servers (llama.cpp, Ollama…),
SearXNG and upstream search engines are separate projects; report issues in
them upstream.
