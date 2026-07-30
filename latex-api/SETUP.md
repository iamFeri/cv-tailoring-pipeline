# latex-api — setup guide

A minimal FastAPI microservice that compiles a `.tex` file to PDF with
`xelatex` and returns the PDF (or a structured error with the compiler log).
It exists so the n8n workflow in this repo can turn a filled-in
`templates/CV2_placeholders.tex` into a PDF without depending on any paid
LaTeX-as-a-service API. It runs as one more container alongside n8n on the
same VPS, reachable only over the internal Docker network — it is never
exposed to the public internet.

## Quickstart (start here if you have nothing set up yet)

Skip to [Deployment](#deployment-docker-compose-service-block) below if you
already run self-hosted n8n via Docker Compose — you just need to add the
`latex-api` service block to your existing stack.

Starting from zero (no VPS, no Docker, no n8n running):

1. **Get a VPS with SSH access.** Any provider works — this whole stack
   (n8n + latex-api) runs comfortably on the smallest tier most providers
   sell (this build uses a €4/month box). You need a fresh Ubuntu 22.04+
   machine and root/sudo SSH access to it.
2. **Install Docker Engine + the Docker Compose plugin** on the VPS. Follow
   Docker's own install guide for your distro —
   [docs.docker.com/engine/install](https://docs.docker.com/engine/install/)
   — rather than a copied command list here, since exact commands drift
   across Docker and Ubuntu versions.
3. **Create a working directory on the VPS** (e.g. `~/stack/`) with a
   `docker-compose.yml` combining n8n and this service — see
   [Deployment](#deployment-docker-compose-service-block) below for the
   exact block. Copy this repo's `latex-api/` folder into that same
   directory, alongside the compose file (`~/stack/latex-api/Dockerfile`
   etc.) — `build: ./latex-api` in the compose file expects it there.
4. **Bring the stack up:** `docker compose up -d`, then `docker compose ps`
   to confirm both containers report healthy.
5. **Confirm latex-api is reachable from n8n** (not from your own machine —
   it deliberately has no public port, see below):
   `docker compose exec n8n curl -f http://latex-api:8000/health` should
   return a 200 response from inside the n8n container. If it doesn't,
   check both containers are on the same `networks:` entry (see
   [Deployment](#deployment-docker-compose-service-block)) and re-check
   `docker compose logs latex-api` for a build/startup error.
6. **Expose n8n itself** behind a reverse proxy with HTTPS (Caddy is the
   simplest option for automatic certificates) if you want to reach the n8n
   editor from outside the VPS — that part is standard n8n self-hosting,
   not specific to this workflow: see
   [n8n's own hosting docs](https://docs.n8n.io/hosting/). `latex-api`
   itself never needs a public route.

Once the stack is up and `latex-api/health` responds, continue in
`TEACHABLE_GUIDE.md` for the workflow-specific setup (Google/Telegram
credentials, importing `workflows/embed-experience-bank.json`).

## Why it's built this way

- **Targeted `apt` install instead of `texlive/texlive:latest`.** The full
  `scheme-full` TeX Live image is 4–7GB+, which doesn't fit on a VPS with
  ~4GB free disk. The `Dockerfile` installs only `texlive-base`,
  `texlive-latex-base/recommended/extra`, `texlive-fonts-recommended`, and
  `texlive-xetex` — the specific packages `CV2_placeholders.tex` needs.
  Total image footprint is closer to 1–1.5GB.
- **`xelatex`, not `pdflatex`.** The CV template uses `fontspec` /
  `\setmainfont`, which only `xelatex` (or `lualatex`) can process.
- **Compiles twice per request.** A second pass is required for LaTeX to
  resolve anything needing a second reference pass; cheap insurance for a
  short one-page CV.
- **No public port.** The service has no `ports:` mapping in
  docker-compose — it's reachable only by other containers on the same
  Docker network, addressed by service name.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | Ubuntu 22.04 base + targeted TeX Live packages + the FastAPI app |
| `main.py` | The FastAPI app: `GET /health`, `POST /convert` |
| `requirements.txt` | Python deps (`fastapi`, `uvicorn`, `python-multipart`) |

## Deployment: docker-compose service block

On the VPS, this runs as one service inside the same compose stack as n8n
(and Caddy as reverse proxy for n8n itself). The relevant excerpt:

```yaml
services:
  n8n:
    image: docker.n8n.io/n8nio/n8n
    # ...existing n8n config...
    networks:
      - n8n_ipv6

  latex-api:
    build: ./latex-api
    restart: always
    networks:
      - n8n_ipv6
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 10s
      timeout: 5s
      retries: 5

networks:
  n8n_ipv6:
    enable_ipv6: true
    ipam:
      config:
        - subnet: "fd00:2:1::/64"
          gateway: "fd00:2:1::1"
```

Key points:
- `build: ./latex-api` means the folder containing this `Dockerfile` must
  sit alongside your compose file (e.g. copy this repo's `latex-api/`
  folder into the same directory as your VPS `docker-compose.yml`, next to
  the `n8n` service definition).
- Both services must share the **same** `networks:` entry (`n8n_ipv6`
  above, or whatever you name it) — that's what makes Docker's internal DNS
  resolve `latex-api` to the container's address from inside n8n.
- No `ports:` block on `latex-api` — don't add one unless you need to hit
  it directly from outside Docker for debugging.

## Calling it from n8n

From an n8n **HTTP Request** node, on the same Docker network:

- URL: `http://latex-api:8000/convert`
- Method: `POST`
- Body type: `multipart/form-data`
- Fields:
  - `main_file` — the compiled `.tex` file (filename must end in `.tex`)
  - `assets` (optional, repeatable) — any additional files the `.tex`
    references (images, `.cls`, `.sty`). All assets land flat in the
    compile working directory — subfolder paths inside the `.tex` are not
    supported by this version.
- Response: the PDF binary on success (`200`), or a JSON body with
  `error`/`log` on failure (`422` compile error, `500` unexpected error,
  `504` timeout).

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `LATEX_ENGINE` | `xelatex` | Compiler binary to invoke |
| `COMPILE_TIMEOUT_SECONDS` | `60` | Hard ceiling per compile pass, so a malformed `.tex` can't hang a worker |

## Local build/test (without n8n)

```bash
cd latex-api
docker build -t latex-api .
docker run --rm -p 8000:8000 latex-api

curl -f http://localhost:8000/health

# Fill in the placeholder template with real values first — this sends the
# raw template as-is, which will fail if placeholder tokens remain unresolved.
curl -X POST http://localhost:8000/convert \
  -F "main_file=@../templates/CV2_placeholders.tex;filename=cv.tex" \
  -o cv.pdf
```

## Note on `cv.tex` / sample input

The service itself is content-agnostic — it compiles whatever `.tex` it's
given. This repo does not ship a filled-in sample CV alongside `latex-api/`
because any realistic sample would contain real personal data (name,
contact details). Use `templates/CV2_placeholders.tex` as the structural
reference, or generate a filled-in `.tex` via the n8n workflow itself
before sending it through `/convert`.
