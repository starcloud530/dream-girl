# Docker (experimental)

> **Status: experimental skeleton only.**  
> The supported one-click path remains **AutoDL shell**: `deploy/autodl/install.sh` → `start_all.sh` on a bare Linux + NVIDIA host.  
> Do **not** expect CI or a cold `docker compose up` to go green on GPU.

## What this directory is

| File | Role |
|------|------|
| `compose.yaml` | Service/port skeleton: `orch` :6006 · `gateway` :6008 · `engine` :6009 · `omni` :8091 |
| `README.md` | This note |

Image tags (`dream-girl/*:experimental`) are placeholders; real Dockerfiles are out of scope for this skeleton.

## Ports (same as AutoDL)

| Service | Compose name | Port |
|---------|--------------|------|
| Orchestrator + Web | `orch` | 6006 |
| FlashHead Gateway | `gateway` | 6008 |
| FlashHead Engine | `engine` | 6009 |
| Qwen-TTS / vLLM-Omni | `omni` | 8091 |

## Weights — never in image layers

- Do **not** `COPY` / bake `.pth` / `.safetensors` into Docker images.
- Mount host weights at runtime via `DREAM_GIRL_MODELS_ROOT` (compose bind: `${DREAM_GIRL_MODELS_ROOT:-./models}:/models:ro`).
- Obtain weights with the AutoDL download flow (or equivalent); keep them outside the repo.

## Intended later usage (not ready)

```bash
# Requires NVIDIA Container Toolkit + real images (future work)
export DREAM_GIRL_MODELS_ROOT=/path/to/models
docker compose -f deploy/docker/compose.yaml --profile gpu config   # validate only
# docker compose -f deploy/docker/compose.yaml --profile gpu up -d  # NOT supported yet
```

## Prefer AutoDL

```bash
cp -n .env.example .env   # fill DEEPSEEK_API_KEY
bash deploy/autodl/install.sh
bash deploy/autodl/start_all.sh
bash deploy/autodl/healthcheck.sh
```

See repo root [README.md](../../README.md) and [docs/architecture.md](../../docs/architecture.md).
