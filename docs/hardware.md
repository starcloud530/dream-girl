# Hardware

## Recommended

- **Single NVIDIA GPU ≥ 24GB** colocating:
  - FlashHead infer 384² → display 512 (frontend face-stack)
  - Qwen3-TTS 0.6B CustomVoice via vLLM-Omni
- Verified: **RTX 5090** on AutoDL (typical ~18GB used with both services)

## Also workable

| GPU | Guidance |
|-----|----------|
| RTX 4090 24GB | Likely OK; keep TTS `gpu_memory_utilization` low (see Omni stage yaml) |
| Smaller VRAM | Use MiniMax/Edge TTS; run only FlashHead locally |
| Multi-machine | Orch+Web anywhere; Gateway/Engine on GPU box; TTS optional remote |

## Not supported (MVP)

- Apple Silicon GPU path for FlashHead
- CPU-only talking head
- Guaranteed multi-tenant concurrency on one card

Mac debug (Orch + Edge/Mock TTS, no talking-head): [mac-debug.md](mac-debug.md).

## Disk layout (AutoDL)

| Path | Use |
|------|-----|
| `/root/autodl-tmp/dream-girl` | Code + logs sibling |
| `/root/autodl-fs/models` | Weights (`flashhead/`, `qwen-tts/`) |

Override with `DREAM_GIRL_ROOT` / `DREAM_GIRL_MODELS_ROOT` / `DREAM_GIRL_LOG_DIR`.
