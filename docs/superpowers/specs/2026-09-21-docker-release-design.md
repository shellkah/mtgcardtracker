# Docker Deployment, Container Packaging, and Releases

**Date:** 2026-09-21
**Status:** Approved design, pending implementation plan
**Parent spec:** `docs/superpowers/specs/2026-09-19-mtg-card-tracker-design.md`

## 1. Summary

The app currently runs only from a checked-out source tree with `uv`: `uv run
uvicorn app.main:app` executes with `app/main.py` as the import root, reading
`config/stores.yaml` and writing `mtgcardtracker.db` **relative to the current
working directory**. There is no `Dockerfile`, no compose file, no CI, and no
release automation.

This change packages the app as a container image published to GitHub Container
Registry (GHCR), ships a Compose file that drops the app onto an existing
Docker Compose server behind an nginx/Nginx Proxy Manager (NPM) reverse proxy,
and adds GitHub Actions for continuous integration and tag-driven multi-arch
releases. The goal: **a server operator running other services under Docker
Compose can run this app with one compose snippet, without cloning the repo or
installing Python.**

## 2. Goals

- Build a small, reproducible, non-root container image from the existing
  `pyproject.toml` / `uv.lock`.
- Publish that image as a multi-arch package (`linux/amd64`, `linux/arm64`) to
  `ghcr.io/shellkah/mtgcardtracker`.
- Provide a `compose.yaml` that joins an existing external proxy network, needs
  no host port, and persists state in a named volume.
- Make the SQLite path and store-config path configurable by environment
  variable so the container can write to a volume while defaults stay unchanged
  for local source runs.
- Run tests on every PR/push and publish versioned images on `v*` tags, with a
  GitHub Release per tag.
- Document the operator flow: pull, configure, `docker compose up -d`, point the
  proxy at the container, upgrade by changing a tag.

## 3. Non-goals

- No change to application behaviour, adapters, matching, SSE, or the frontend.
- No Kubernetes, Helm, or Swarm manifests.
- No image signing (cosign) or SBOM/provenance attestations in this change.
- No automated deployment to any server (no watchtower, no webhook deploys).
- No database migrations or new persistence layer — SQLite stays.
- No auth/accounts added to the app; it remains single-user and assumes it sits
  behind a proxy the operator controls.

## 4. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Image scope | One service serves API + static frontend | FastAPI already mounts `web/`; no second container or JS build step needed. |
| Build strategy | Multi-stage: uv builder installs locked deps into `/opt/venv`; `python:3.12-slim` runtime copies it | Lockfile-exact, no uv/compilers in the final image, small pull for home servers. |
| Project install | `uv sync --frozen --no-dev` only (no project install) | `pyproject.toml` sets `package = false`; deps-only is correct and fastest. |
| Base mismatch risk | Keep builder and runtime on the same Debian release (bookworm) | The copied venv links against the builder's glibc; matching suites avoids breakage. |
| Runtime user | Non-root `app` (uid 1000) | Standard hardening; `/data` is pre-created owned by `app` for volume init. |
| Healthcheck | stdlib `urllib` probe against new `/healthz` | `python:3.12-slim` has no curl/wget; a Python probe adds no dependency. |
| Config/DB paths | Env vars `MTG_CONFIG_PATH`, `MTG_DB_PATH`, defaults unchanged | Container mounts volume at `/data`; `create_app` stays pure for tests. |
| Persistence | Named volume `mtg-data:/data` | Survives image replacement; no host file-permission pitfalls. |
| Store config | Baked into image; overridable via bind mount | Works out of the box; operators can still supply a custom `stores.yaml`. |
| Network | External network, no host port (`expose: 8000`) | User runs nginx/NPM on the same server; the proxy reaches the container by name. |
| Registry | GHCR with built-in `GITHUB_TOKEN` | No extra secrets; repository-scoped and already authenticated in Actions. |
| Architectures | `linux/amd64` + `linux/arm64` | Covers x86 servers and ARM home boxes. |
| Release trigger | Push of `v*` tags; CI on PR + push to `master` | Conventional: merging is cheap, releases are deliberate. |
| First release | `v0.1.0` (matches `pyproject.toml` version) | Keeps tag and package version aligned. |

## 5. Architecture

New files:

```
Dockerfile                          # multi-stage build
.dockerignore                       # keep build context small
compose.yaml                        # operator deployment
.env.example                        # MTG_TAG, PROXY_NETWORK
.github/workflows/ci.yml            # test + build-check on PR/main
.github/workflows/release.yml       # multi-arch push + GitHub Release on v* tag
docs/superpowers/specs/2026-09-21-docker-release-design.md
```

Modified files:

```
app/main.py                         # /healthz route; env-driven module-level create_app
README.md                           # "Deploy with Docker" section
```

### 5.1 Image

Stage `builder`:

- Base: `ghcr.io/astral-sh/uv:<pinned>-python3.12-bookworm-slim`.
- `UV_PROJECT_ENVIRONMENT=/opt/venv`, `UV_COMPILE_BYTECODE=1`,
  `UV_LINK_MODE=copy`.
- Copy `pyproject.toml` and `uv.lock` first, then `uv sync --frozen --no-dev`.
  `package = false` means only the four runtime dependencies
  (`fastapi`, `uvicorn[standard]`, `httpx`, `PyYAML`) land in the venv.

Stage `runtime`:

- Base: `python:3.12-slim-bookworm`.
- Copy `/opt/venv` from builder; prepend to `PATH`.
- Create user `app` (uid 1000). Copy `app/`, `web/`, `config/`, `cli.py` and
  set ownership.
- Create `/data` owned by `app` (Docker copies this ownership into a fresh named
  volume).
- `WORKDIR /app`; `ENV MTG_CONFIG_PATH=config/stores.yaml`,
  `MTG_DB_PATH=/data/mtgcardtracker.db`.
- `USER app`; `EXPOSE 8000`.
- `HEALTHCHECK` runs
  `python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"`.
- `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`.

### 5.2 App changes

`app/main.py` gains one route inside `create_app`, declared before the static
mount so it is not shadowed by `StaticFiles`:

```python
@app.get("/healthz")
def healthz():
    return {"status": "ok"}
```

The module-level app is created from environment variables, leaving
`create_app`'s signature and defaults untouched so existing tests that pass
explicit paths keep working:

```python
app = create_app(
    config_path=os.environ.get("MTG_CONFIG_PATH", "config/stores.yaml"),
    db_path=os.environ.get("MTG_DB_PATH", "mtgcardtracker.db"),
)
```

### 5.3 Compose

`compose.yaml` defines one service:

- `image: ghcr.io/shellkah/mtgcardtracker:${MTG_TAG:-latest}` with a commented
  `build: .` alternative for local builds.
- `container_name: mtgcardtracker` so the proxy can target the name.
- `restart: unless-stopped`.
- `environment`: `MTG_DB_PATH=/data/mtgcardtracker.db`,
  `MTG_CONFIG_PATH=/app/config/stores.yaml`.
- `volumes`: `mtg-data:/data`; a commented read-only bind mount at
  `/app/config/stores.yaml` for custom store config.
- `networks`: `proxy` external, `name: ${PROXY_NETWORK:-proxy}`.
- `expose: ["8000"]` and **no `ports:`** — only the proxy network reaches it.
- Top-level named volume `mtg-data`.

`.env.example`:

```
# Image tag to run. Use a pinned version (e.g. 1.2.3) or `latest`.
MTG_TAG=latest

# Name of the existing external Docker network your nginx/NPM proxy uses.
PROXY_NETWORK=proxy
```

### 5.4 CI (`ci.yml`)

- `on: pull_request` and `push` to `master`.
- `test` job: checkout → `astral-sh/setup-uv` (cache enabled) →
  `uv sync --frozen` → `uv run pytest -v`.
- `build` job: checkout → `docker/setup-buildx-action` →
  `docker/build-push-action` with `push: false`, `platforms: linux/amd64` (CI
  only validates the Dockerfile; multi-arch happens on release). This fails
  fast if the image stops building.

### 5.5 Release (`release.yml`)

- `on: push: tags: ["v*"]`.
- `permissions: contents: write`, `packages: write`.
- Steps: checkout → `docker/setup-qemu-action` → `docker/setup-buildx-action` →
  `docker/login-action` to `ghcr.io` with `github.actor` / `GITHUB_TOKEN` →
  `docker/metadata-action` (semver tags: `{{version}}`, `{{major}}.{{minor}}`,
  `latest`) → `docker/build-push-action` with
  `platforms: linux/amd64,linux/arm64`, `push: true` → create a GitHub Release
  for the tag with generated notes (`gh release create "$GITHUB_REF_NAME"
  --generate-notes`, authenticated by the job's `GITHUB_TOKEN`).
- Output tags for `v1.2.3`: `ghcr.io/shellkah/mtgcardtracker:1.2.3`,
  `:1.2`, `:latest`.

### 5.6 Documentation

Add a **"Deploy with Docker"** section to `README.md` covering:

- Prerequisites: Docker Engine + Compose plugin, an existing nginx/NPM proxy on
  the same host, network name it uses.
- Steps: copy `compose.yaml` and `.env.example` (as `.env`), set
  `PROXY_NETWORK` (and pin `MTG_TAG`), `docker compose up -d`.
- Configure NPM: forward hostname to `mtgcardtracker`, port `8000`, scheme
  `http`.
- Custom store config: bind-mount a `stores.yaml` at
  `/app/config/stores.yaml`.
- Data: SQLite lives in the `mtg-data` volume; `docker compose down` keeps it,
  `down -v` deletes it.
- Upgrade/rollback: change `MTG_TAG`, `docker compose pull && docker compose up -d`.
- Package visibility: GHCR packages default to private; set the package public
  or `docker login ghcr.io` on the server.

## 6. Data flow

Build time: GitHub Actions checks out the repo, uv resolves `uv.lock`, the
image is built and pushed to GHCR. Runtime: the operator's compose pulls the
image, starts the container on the proxy network, and the app loads
`/app/config/stores.yaml`, opens `/data/mtgcardtracker.db` (on the named
volume), serves the API and static frontend on `:8000`. nginx/NPM forwards
external traffic to `mtgcardtracker:8000`. On shutdown/recreate the volume
retains cache, saved lists, and satchel rows.

## 7. Error handling

- **Missing config file:** unchanged app behaviour — `load_config` raises at
  startup and the container exits; `restart: unless-stopped` surfaces the crash
  loop. Covered by the docs pointing at `MTG_CONFIG_PATH`.
- **Unwritable data dir:** SQLite fails on first write; `/data` is pre-created
  owned by `app` so a fresh named volume is writable. Bind-mount users are told
  to ensure ownership.
- **Proxy cannot reach app:** because there is no host port, a misconfigured
  external network name is the most likely failure. Docs make `PROXY_NETWORK`
  explicit and suggest `docker network ls`.
- **Healthcheck:** `/healthz` returns 200 once the app is serving; the probe
  fails softly while starting (Docker retries during `start_period`).
- **Image builder/runtime mismatch:** mitigated by pinning both stages to
  bookworm; CI builds the image on every PR to catch a broken Dockerfile early.

## 8. Testing

- `uv run pytest` stays green after the `app/main.py` change (the existing
  tests pass explicit paths; add a small test asserting `/healthz` returns 200
  and JSON `{"status": "ok"}` via `TestClient`).
- `docker build .` succeeds locally.
- Run the container and confirm `/healthz` and `/` respond.
- `docker compose config` validates with a dummy external network present.
- CI runs the test job and the build job on the PR; a `v0.1.0` tag exercises
  the multi-arch release path.

## 9. Rollout

1. Implement image + app changes; verify locally.
2. Add CI; confirm the PR runs test and build jobs.
3. Add release workflow; merge.
4. Tag `v0.1.0`; confirm GHCR shows a multi-arch package and a GitHub Release.
5. Set the GHCR package public (one-time, manual) so pullers need no login.
