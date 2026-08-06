# Mac local debug (no talking-head)

MVP **guarantees** AutoDL Linux + NVIDIA only. On macOS (Intel / Apple Silicon) you can run **Orchestrator + Web** for LLM/TTS and UI smoke — **not** FlashHead / talking-head.

| Included | Not included |
|----------|--------------|
| Orchestrator + static Web (`:6006`) | FlashHead Gateway / Engine |
| Edge TTS or Mock TTS | Qwen/vLLM-Omni (needs NVIDIA) |
| DeepSeek LLM (or Mock) | Lip-sync / MSE face stream |

See also: [hardware.md](hardware.md) · [providers.md](providers.md).

## One-liner

```bash
# From repo root → app/
cd app
cp -n ../.env.example .env   # optional: fill DEEPSEEK_API_KEY for real LLM
# Edit config/app.yaml: avatar_mode: "browser", tts.provider: "edge" (or "mock")
bash scripts/start.sh
# Open http://127.0.0.1:6006
```

`app/scripts/start.sh` creates `.venv`, installs `app/` deps, and starts `python -m services.orchestrator.main`. It is an **optional debug entry**, not the AutoDL one-click path (`deploy/autodl/`).

## Config knobs

In `app/config/app.yaml` (or copy from `configs/app.example.yaml`):

```yaml
avatar_mode: "browser"   # NoOp avatar — no GPU gateway
tts:
  provider: "edge"       # or "mock"; do not use "qwen" without Omni
```

| Mode | How | Result |
|------|-----|--------|
| Mock (offline) | `CYBER_GF_USE_MOCK=1` | Mock LLM + Mock TTS; browser avatar |
| Edge TTS | `tts.provider: edge` + unset mock | Real Edge speech; needs network |
| Real LLM | `DEEPSEEK_API_KEY` in `.env` | DeepSeek replies; else falls back to mock LLM |

With `avatar_mode: browser` (or mock), Orchestrator uses `NoOpAvatarClient` — **no talking-head**, audio/text only in the browser.

## Smoke scripts (optional)

```bash
cd app
bash scripts/e2e_smoke.sh   # mock LLM/TTS; default ORCH_PORT=8011
# With keys + edge:
# unset CYBER_GF_USE_MOCK && bash scripts/e2e_real.sh
```

Note: smoke scripts default to port **8011**; `start.sh` / `app.yaml` default to **6006**. Align `ORCH_PORT` / `orchestrator.port` if you mix them.

## Explicit non-goals

- Do **not** expect FlashHead on Apple Silicon or CPU-only lip-sync.
- Do **not** run `deploy/autodl/start_all.sh` as the Mac path.
- Full face + Qwen TTS: use a GPU box and the AutoDL install flow in the README.
