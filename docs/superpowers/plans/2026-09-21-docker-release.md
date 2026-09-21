# Docker Deployment, Container Packaging, and Releases — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package MTG Card Tracker as a multi-arch GHCR image, deploy it with a Compose file behind an existing nginx/NPM proxy, and automate test/build on PRs plus versioned releases on `v*` tags.

**Architecture:** One multi-stage Docker image (uv builder installs the locked runtime venv; `python:3.12-slim` runtime copies it and runs as a non-root user). The app gains an env-driven module-level factory and a `/healthz` probe. A `compose.yaml` attaches the container to an external proxy network with a named data volume, and two GitHub Actions workflows test/build on PRs and publish multi-arch images plus a GitHub Release on tags.

**Tech Stack:** Docker / Buildx, Docker Compose, GitHub Actions, `ghcr.io/astral-sh/uv`, Python 3.12, FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-09-21-docker-release-design.md`

## Global Constraints

- Runtime base image: `python:3.12-slim-bookworm`; builder image `ghcr.io/astral-sh/uv:0.9.15-python3.12-bookworm-slim` (both Debian bookworm — do not diverge).
- Image name: `ghcr.io/shellkah/mtgcardtracker`.
- Release platforms: `linux/amd64` and `linux/arm64`.
- Env var names are exactly `MTG_CONFIG_PATH` and `MTG_DB_PATH`; defaults stay `config/stores.yaml` and `mtgcardtracker.db`.
- `compose.yaml` must **not** publish host ports — only `expose: 8000`.
- External proxy network name comes from `${PROXY_NETWORK:-proxy}`.
- Data lives in the named volume `mtg-data` mounted at `/data`.
- GitHub Actions pinned to these majors: `actions/checkout@v7`, `astral-sh/setup-uv@v10`, `docker/setup-buildx-action@v4`, `docker/setup-qemu-action@v4`, `docker/login-action@v4`, `docker/metadata-action@v6`, `docker/build-push-action@v7`.
- Python >= 3.11; run tests with `uv run pytest -v`.
- Current branch is `feature/mtg-card-tracker`; one commit per task.

---

### Task 1: Health endpoint and env-configurable paths

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_deploy.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces: `GET /healthz` → `200 {"status": "ok"}`; module-level `app.main.app` built from `MTG_CONFIG_PATH` / `MTG_DB_PATH`. `create_app(config_path, db_path)` signature is unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_deploy.py`:

```python
import importlib

from fastapi.testclient import TestClient

from app.main import create_app


def _stores_yaml(tmp_path, store_id="greyogre"):
    stores = tmp_path / "stores.yaml"
    stores.write_text(
        "zones:\n  - id: singapore\n    name: Singapore\n"
        "stores:\n"
        f"  - id: {store_id}\n    name: Env Store\n    zone: singapore\n"
        "    adapter: app.adapters.greyogre:GreyOgreAdapter\n"
        "    base_url: https://example.test\n    enabled: true\n"
    )
    return stores


def test_healthz_ok(tmp_path):
    stores = _stores_yaml(tmp_path)
    client = TestClient(create_app(config_path=str(stores), db_path=str(tmp_path / "t.db")))
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_module_app_honours_env_paths(tmp_path, monkeypatch):
    stores = _stores_yaml(tmp_path, store_id="envstore")
    monkeypatch.setenv("MTG_CONFIG_PATH", str(stores))
    monkeypatch.setenv("MTG_DB_PATH", str(tmp_path / "env.db"))

    import app.main as main_module

    importlib.reload(main_module)
    client = TestClient(main_module.app)
    data = client.get("/api/stores").json()
    assert data["stores"][0]["id"] == "envstore"
    assert (tmp_path / "env.db").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_deploy.py -v`
Expected: both FAIL — `test_healthz_ok` gets 404 (no route), `test_module_app_honours_env_paths` fails because the module app ignores the env config.

- [ ] **Step 3: Add the `/healthz` route**

In `app/main.py`, immediately after `registry = RunRegistry()` (`create_app` body) and before the `@app.get("/api/stores")` definition, insert:

```python
    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}
```

- [ ] **Step 4: Read env paths at module level**

Add `import os` to the top imports of `app/main.py` (between `import json` and `from pathlib import Path`). Then replace the final line:

```python
app = create_app()
```

with:

```python
app = create_app(
    config_path=os.environ.get("MTG_CONFIG_PATH", "config/stores.yaml"),
    db_path=os.environ.get("MTG_DB_PATH", "mtgcardtracker.db"),
)
```

Do **not** change `create_app`'s signature — tests and local runs rely on the existing defaults.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_deploy.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/main.py tests/test_deploy.py
git commit -m "feat: add /healthz and env-configurable app paths"
```

---

### Task 2: Container image

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

**Interfaces:**
- Consumes: `/healthz` from Task 1; env vars from Task 1.
- Produces: image listening on `:8000`, healthcheck hitting `/healthz`, non-root `app` user, venv at `/opt/venv`, `/data` owned by `app`.

- [ ] **Step 1: Write `.dockerignore`**

Create `.dockerignore`:

```
.git
.github
.venv
__pycache__
**/__pycache__
.pytest_cache
*.db
tests
docs
*.md
.env
.env.*
```

- [ ] **Step 2: Write `Dockerfile`**

Create `Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1

# ---- builder: resolve the locked runtime environment -----------------------
FROM ghcr.io/astral-sh/uv:0.9.15-python3.12-bookworm-slim AS builder

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# ---- runtime: minimal image with just the venv and the app -----------------
FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MTG_CONFIG_PATH=config/stores.yaml \
    MTG_DB_PATH=/data/mtgcardtracker.db

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY web ./web
COPY config ./config
COPY cli.py ./cli.py

RUN mkdir -p /data && chown app:app /data

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Build the image**

Run: `docker build -t mtgcardtracker:local .`
Expected: build succeeds; final image is based on `python:3.12-slim-bookworm`.

- [ ] **Step 4: Run the container and probe it**

```bash
docker run -d --rm --name mtg-local -p 18000:8000 mtgcardtracker:local
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:18000/healthz && break; sleep 1; done
curl -fsS http://127.0.0.1:18000/ | head -1
docker inspect --format '{{.State.Health.Status}}' mtg-local
```

Expected: `/healthz` prints `{"status":"ok"}`, `/` returns the HTML page, health status eventually `healthy`.

- [ ] **Step 5: Verify persistence target and non-root user**

```bash
docker exec mtg-local id -u
docker exec mtg-local test -w /data && echo "data writable"
```

Expected: prints `1000` and `data writable`.

- [ ] **Step 6: Tear down**

```bash
docker rm -f mtg-local
```

- [ ] **Step 7: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "build: add multi-stage container image"
```

---

### Task 3: Compose deployment

**Files:**
- Create: `compose.yaml`
- Create: `.env.example`

**Interfaces:**
- Consumes: the image from Task 2 (name `ghcr.io/shellkah/mtgcardtracker`).
- Produces: service `mtgcardtracker` on external network `${PROXY_NETWORK:-proxy}`, volume `mtg-data:/data`, `expose: 8000`, no host ports.

- [ ] **Step 1: Write `.env.example`**

Create `.env.example`:

```
# Image tag to run. Pin a release (e.g. 0.1.0) or use `latest`.
MTG_TAG=latest

# Name of the existing external Docker network your nginx/NPM proxy uses.
# Find it with: docker network ls
PROXY_NETWORK=proxy
```

- [ ] **Step 2: Write `compose.yaml`**

Create `compose.yaml`:

```yaml
services:
  mtgcardtracker:
    image: ghcr.io/shellkah/mtgcardtracker:${MTG_TAG:-latest}
    # build: .            # uncomment to build locally instead of pulling
    container_name: mtgcardtracker
    restart: unless-stopped
    environment:
      MTG_DB_PATH: /data/mtgcardtracker.db
      MTG_CONFIG_PATH: /app/config/stores.yaml
    volumes:
      - mtg-data:/data
      # - ./stores.yaml:/app/config/stores.yaml:ro   # custom store config
    expose:
      - "8000"
    networks:
      - proxy

networks:
  proxy:
    external: true
    name: ${PROXY_NETWORK:-proxy}

volumes:
  mtg-data:
```

- [ ] **Step 3: Validate the compose file**

```bash
docker network create mtgtest-proxy
MTG_TAG=local PROXY_NETWORK=mtgtest-proxy docker compose config
```

Expected: normalized config prints; the service has `expose: 8000` and **no** `ports`, and the network is `external: true, name: mtgtest-proxy`.

- [ ] **Step 4: End-to-end through the proxy network**

```bash
docker tag mtgcardtracker:local ghcr.io/shellkah/mtgcardtracker:local
MTG_TAG=local PROXY_NETWORK=mtgtest-proxy docker compose up -d
docker run --rm --network mtgtest-proxy curlimages/curl -fsS http://mtgcardtracker:8000/healthz
docker inspect --format '{{.State.Health.Status}}' mtgcardtracker
```

Expected: `{"status":"ok"}`, then `healthy`.

- [ ] **Step 5: Tear down test artifacts**

```bash
MTG_TAG=local PROXY_NETWORK=mtgtest-proxy docker compose down
docker network rm mtgtest-proxy
docker image rm ghcr.io/shellkah/mtgcardtracker:local
```

Expected: container/volume removed; the named volume `mtg-data` may remain and is harmless.

- [ ] **Step 6: Commit**

```bash
git add compose.yaml .env.example
git commit -m "feat: add compose deployment for proxy networks"
```

---

### Task 4: CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `uv.lock` (test job) and the `Dockerfile` (build job).
- Produces: required checks `test` and `build` on pull requests and pushes to `master`.

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [master]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10
        with:
          enable-cache: true
      - run: uv sync --frozen
      - run: uv run pytest -v

  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: docker/setup-buildx-action@v4
      - uses: docker/build-push-action@v7
        with:
          context: .
          push: false
          platforms: linux/amd64
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 2: Verify the YAML parses**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo OK`
Expected: `OK`.

- [ ] **Step 3: Lint with actionlint (if available)**

Run: `docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:latest .github/workflows/ci.yml`
Expected: no output (no findings). If the image cannot be pulled, skip — YAML parse already passed.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: test and build-check on pull requests"
```

---

### Task 5: Release workflow

**Files:**
- Create: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: the `Dockerfile` and the image name `ghcr.io/shellkah/mtgcardtracker`.
- Produces: on a `v*` tag, a multi-arch GHCR push with tags `<version>`, `<major>.<minor>`, `latest`, plus a GitHub Release.

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/release.yml`:

```yaml
name: Release

on:
  push:
    tags: ["v*"]

permissions:
  contents: write
  packages: write

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7

      - uses: docker/setup-qemu-action@v4

      - uses: docker/setup-buildx-action@v4

      - uses: docker/login-action@v4
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - id: meta
        uses: docker/metadata-action@v6
        with:
          images: ghcr.io/shellkah/mtgcardtracker
          tags: |
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=raw,value=latest

      - uses: docker/build-push-action@v7
        with:
          context: .
          push: true
          platforms: linux/amd64,linux/arm64
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}

      - name: Create GitHub Release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: gh release create "$GITHUB_REF_NAME" --generate-notes
```

- [ ] **Step 2: Verify the YAML parses**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))" && echo OK`
Expected: `OK`.

- [ ] **Step 3: Lint with actionlint (if available)**

Run: `docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:latest .github/workflows/release.yml`
Expected: no output. Skip if the image cannot be pulled.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: publish multi-arch images and releases on tags"
```

---

### Task 6: Operator documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: `compose.yaml`, `.env.example`, `MTG_CONFIG_PATH`, `MTG_DB_PATH`.
- Produces: a "Deploy with Docker" README section operators can follow without the source tree.

- [ ] **Step 1: Add the deploy section**

In `README.md`, insert the following after the `## Run` section (before `## Card list formats`):

````markdown
## Deploy with Docker

The image is published to GitHub Container Registry and runs the API and
frontend in one container. It is designed to sit behind an existing nginx /
Nginx Proxy Manager (NPM) reverse proxy on a Docker Compose server and needs no
host port.

### Prerequisites

- Docker Engine with the Compose plugin.
- A running nginx/NPM proxy that shares a Docker network with your apps.
- The name of that network (`docker network ls`).

### Run

1. Copy `compose.yaml` and `.env.example` (renamed to `.env`) onto the server.
2. Edit `.env`:
   - `PROXY_NETWORK` — the external network your proxy uses.
   - `MTG_TAG` — pin a release (e.g. `0.1.0`) or leave `latest`.
3. Start it:

   ```bash
   docker compose up -d
   ```

4. In nginx/NPM, create a proxy host forwarding to **Hostname**
   `mtgcardtracker`, **Port** `8000`, **Scheme** `http`. The proxy and the app
   must both be attached to `PROXY_NETWORK`.

### Data and configuration

- All state (SQLite cache, saved lists, the Satchel) lives in the named volume
  `mtg-data` at `/data`. `docker compose down` keeps it; `docker compose down -v`
  deletes it.
- `config/stores.yaml` is baked into the image. To customise stores, uncomment
  the volume line in `compose.yaml` and mount your own file read-only:

  ```yaml
  volumes:
    - ./stores.yaml:/app/config/stores.yaml:ro
  ```

  The mounted file must be readable by uid `1000` (the container's `app` user).

### Upgrade and rollback

Set `MTG_TAG` to the release you want, then:

```bash
docker compose pull && docker compose up -d
```

Rolling back uses the same commands with an earlier tag.

### Package visibility

GHCR packages are private by default. Either set the package visibility to
public in the repository's package settings, or authenticate the server once:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u <username> --password-stdin
```

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `MTG_DB_PATH` | `mtgcardtracker.db` | SQLite file path. Set to `/data/mtgcardtracker.db` in the image. |
| `MTG_CONFIG_PATH` | `config/stores.yaml` | Store config path. Set to `/app/config/stores.yaml` in the image. |
````

- [ ] **Step 2: Verify the section renders references**

Run: `grep -n "Deploy with Docker\|PROXY_NETWORK\|MTG_DB_PATH\|mtg-data" README.md`
Expected: matches for the new heading, the network variable, the DB variable, and the volume name.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document docker deployment and releases"
```

---

## Release verification (after merge, manual)

These do not belong to a code task; they prove the release path.

1. Tag the release: `git tag v0.1.0 && git push origin v0.1.0` (on `master`).
2. Confirm the `Release` workflow succeeds and GHCR shows the package with both
   `linux/amd64` and `linux/arm64` manifests.
3. Confirm a GitHub Release named `v0.1.0` exists with generated notes.
4. Set the package visibility to public (one-time) so pullers need no login.
