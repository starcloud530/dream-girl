# Dream Girl / 赛博女友

**Single-GPU near-realtime talking avatar MVP** — LLM + local/cloud TTS + FlashHead lip-sync, delivered over public **MSE + fMP4** (HTTP).

> Open-source focus: **one-click deploy** on AutoDL-like machines. Swappable LLM / TTS / Avatar backends so the stack can evolve without rewriting the browser path.

## Status

`v0.1.0-mvp` — verified on **NVIDIA RTX 5090** (AutoDL). Not a multi-tenant SaaS.

## Architecture (current)

```text
Browser (MSE/fMP4)
    │  HTTPS :8443 → :6006
    ▼
Orchestrator  ──stream──►  DeepSeek (LLM)
    │                      Qwen3-TTS via vLLM-Omni :8091
    │                      (fallback: MiniMax / Edge)
    ▼ PCM HTTP
FlashHead Gateway :6008 ──► Engine :6009  (~1.4s slices @ 384²)
```

Details: [docs/architecture.md](docs/architecture.md) · providers: [docs/providers.md](docs/providers.md) · hardware: [docs/hardware.md](docs/hardware.md).

## Hardware

| Setup | Notes |
|-------|--------|
| **Recommended** | Single GPU **≥24GB** (5090 / 4090) colocating FlashHead 384 + Qwen-TTS 0.6B |
| Split machines | TTS cloud (MiniMax) + local face only |
| No GPU | Text chat only; no talking-head |

## Quick start (AutoDL)

```bash
# On the GPU machine
git clone https://github.com/starcloud530/dream-girl.git
cd dream-girl
cp .env.example .env   # fill DEEPSEEK_API_KEY
bash deploy/autodl/install.sh   # app deps + FlashHead env + weights + vLLM-Omni
bash deploy/autodl/start_all.sh # Engine :6009 → GW :6008 → TTS :8091 → Orch :6006
bash deploy/autodl/healthcheck.sh
```

`install.sh` downloads FlashHead + Qwen3-TTS **0.6B** into `DREAM_GIRL_MODELS_ROOT` (default `/root/autodl-fs/models`) and bootstraps vLLM-Omni. Use `SKIP_VLLM_OMNI=1` only if you switch `tts.provider` to `minimax`/`edge`.

**Weights (re-runnable):** `bash deploy/autodl/download_weights.sh` — FlashHead + Qwen3-TTS 0.6B (ModelScope first for TTS; `HF_ENDPOINT` for HF mirror). After a dropped connection, re-run the same command. `SKIP_DOWNLOAD=1 bash deploy/autodl/install.sh` skips downloads during install.

Open the AutoDL custom service mapped to **port 6006** (HTTPS portal).

## Layout

| Path | Role |
|------|------|
| `app/` | Orchestrator, providers, web UI |
| `vendor/flashhead/` | FlashHead engine/gateway (HTTP only from app) |
| `configs/app.example.yaml` | Sanitized runtime config |
| `deploy/autodl/` | One-click install / start / health |
| `assets/character/` | Demo portrait (replace for production) |

## License

Apache-2.0 for this repository's orchestration and deploy scripts. Third-party models/runtimes: see [NOTICE](NOTICE).
