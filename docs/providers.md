# Providers (swappable stack)

Providers live under `app/packages/providers/` and are selected by config / env.

## LLM

| Provider | Config | Notes |
|----------|--------|-------|
| DeepSeek | `DEEPSEEK_API_KEY` | Responses API for flash models |
| Mock | `tts/llm mock` via env flag | Offline UI smoke |

Factory: `app/packages/providers/factory.py` → `build_llm()`.

## TTS

| Provider | `tts.provider` | Notes |
|----------|----------------|-------|
| Qwen (vLLM-Omni) | `qwen` / `auto` | Local `:8091`; `install.sh` installs Omni + 0.6B weights |
| MiniMax | `minimax` / `auto` | Needs `MINIMAX_API_KEY`; skip Omni with `SKIP_VLLM_OMNI=1` |
| Edge | `edge` / fallback | No GPU |

Default `start_all.sh` **exits non-zero** if `tts.provider=qwen` and Omni is down. Set provider to `minimax`/`edge` to skip.

Add a new TTS by implementing `synthesize_text_stream(token_iter)` and registering in `factory.py`.

## Avatar backend

| Backend | `avatar_backend` | Transport |
|---------|------------------|-----------|
| FlashHead | `flashhead` | HTTP Gateway in `vendor/flashhead` |
| Browser noop | `avatar_mode: browser` | Client-side audio only |

**Do not** import `vendor/flashhead` Python modules from `app/`. Keep HTTP isolation so backends can change.

## Config knobs (face latency)

Set in environment or `deploy/autodl/start_all.sh`:

- `FACE_PREROLL_MS` (default 1400)
- `FACE_OUT_MS` / `FACE_OUT_FOLLOW_MS` (default 1400)
- `FACE_MSE_FORMAT=fmp4`
- `FLASHHEAD_CONFIG=configs/t1_384_stack.yaml`
