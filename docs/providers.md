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

### Adding a TTS provider

Goal: swap TTS without touching the Web MSE path or Avatar Gateway.

#### 1. Interface (`TTSProvider`)

Implement the protocol in `app/packages/interfaces/__init__.py`:

| Method | Role |
|--------|------|
| `synthesize_stream(text, *, sample_rate=16000, cancel_event=None)` | One-shot text → PCM chunks |
| `synthesize_text_stream(text_iter, *, sample_rate=16000, cancel_event=None)` | Token/sentence stream → PCM (pipeline uses this) |

PCM contract for Avatar ingest: **16-bit LE mono** (`s16le`), typically **16 kHz**. Resample inside the provider if the upstream API uses another rate.

Minimal stub: [`app/packages/providers/_template_tts.py`](../app/packages/providers/_template_tts.py) (copy-paste starter; not wired into the factory by default).

#### 2. Factory registration

In `app/packages/providers/factory.py` → `build_tts()`:

1. Import your class.
2. Add an explicit branch for `cfg.tts_provider == "<name>"`.
3. Optionally extend `auto` probing (health check / key present) **before** the Edge fallback.

Return `(provider_instance, "<name>")` so Orchestrator logs and health can show which stack is live.

#### 3. YAML / config

1. Add a typed config dataclass (e.g. `MyTTSConfig`) in `app/packages/config.py` and parse it from the `tts:` block.
2. Expose knobs in `configs/app.example.yaml` (and your runtime `app/config/app.yaml`):

```yaml
tts:
  provider: "my_tts"   # or auto / qwen / minimax / edge / mock
  my_tts:
    base_url: "http://127.0.0.1:9xxx"
    # api_key via env, never commit secrets
```

3. Prefer env for secrets (`MY_TTS_API_KEY`), not committed yaml.

Reference implementations: `mock.py` (offline), `edge_tts.py` (no GPU), `qwen_tts.py` / `minimax.py` (production-shaped).

## Avatar backend

| Backend | `avatar_backend` | Transport |
|---------|------------------|-----------|
| FlashHead | `flashhead` | HTTP Gateway in `vendor/flashhead` |
| Browser noop | `avatar_mode: browser` | Client-side audio only |

Orchestrator talks to Avatar via `HttpAvatarClient` (`app/packages/avatar_client/client.py`), which POSTs/GETs the Gateway **base URL** (`avatar_gateway.public_url` → `AppConfig.avatar_public_url`).

### Hard rule: HTTP only — no vendor imports

`app/` **must not** import FlashHead internals:

```python
# FORBIDDEN anywhere under app/
from vendor.flashhead import ...
import vendor.flashhead
```

Only the OpenAPI HTTP/SSE contract is allowed. Contract source of truth:
[`app/contracts/openapi/avatar.yaml`](../app/contracts/openapi/avatar.yaml)
(aligned with `vendor/flashhead/serve/gateway.py`).

### Implementing another avatar backend

To plug in EchoMimic / another talking-head stack **without** changing the Web MSE client:

1. **Ship a Gateway** that speaks the same HTTP contract (your process may wrap any engine).
2. **Do not** vendor EchoMimic (or other face) source/weights into this repo for MVP.
3. Point Orchestrator at the new Gateway URL; keep `avatar_mode: gpu`.
4. Leave the browser path (MSE + fMP4 via SSE) unchanged.

#### Required endpoints (must compatible)

| Method | Path | Role |
|--------|------|------|
| `POST` | `/v1/avatar/session` | Create session → `{ "session_id": "..." }` |
| `POST` | `/v1/avatar/{session_id}/audio` | Ingest PCM (`?end=1` to flush) |
| `GET` | `/v1/avatar/{session_id}/av/sse` | SSE of `av_mp4` metadata (chunk, url, duration_ms, format) |
| `GET` | `/v1/avatar/{session_id}/mp4/{n}` | Fetch fMP4/MP4 segment bytes for MSE |

Also implement for parity with the OpenAPI / client (recommended):

- `GET /v1/health`
- `POST /v1/avatar/{session_id}/interrupt`

PCM ingest headers used by `HttpAvatarClient`:
`Content-Type: application/octet-stream`,
`X-Audio-Format: audio/L16; rate=16000; channels=1`.

#### Optional config: `avatar_backend: custom`

Document intent with an external Gateway `base_url` (no in-repo engine):

```yaml
avatar_mode: "gpu"
avatar_backend: "custom"   # not flashhead; your Gateway elsewhere

avatar_gateway:
  host: "0.0.0.0"
  port: 6008
  # Orchestrator / browser use this URL for session, audio, SSE, mp4
  public_url: "http://127.0.0.1:6010"   # example: alternate Gateway on another port
```

Checklist before declaring a backend “compatible”:

- [ ] `POST /v1/avatar/session` returns `session_id`
- [ ] `POST .../audio` accepts 16 kHz s16le PCM; `?end=1` flushes
- [ ] `GET .../av/sse` emits chunk metadata the Web MSE client understands
- [ ] `GET .../mp4/{n}` serves segment bytes (`video/mp4`)
- [ ] No `from vendor.flashhead` under `app/`
- [ ] No EchoMimic (or other face) code/weights committed to this repository

## Config knobs (face latency)

Set in environment or `deploy/autodl/start_all.sh`:

- `FACE_PREROLL_MS` (default 1400)
- `FACE_OUT_MS` / `FACE_OUT_FOLLOW_MS` (default 1400)
- `FACE_MSE_FORMAT=fmp4`
- `FLASHHEAD_CONFIG=configs/t1_384_stack.yaml`