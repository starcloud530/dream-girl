# Architecture

Dream Girl MVP is a **slice-based talking-head pipeline**, not a WebRTC frame stream.

## Components

| Port | Process | Role |
|------|---------|------|
| 6006 | Orchestrator + static Web | LLM/TTS orchestration, MSE client |
| 6008 | FlashHead Gateway | PCM ingest, mux fMP4, SSE |
| 6009 | FlashHead Engine | ~1.4s audio slices @ 384² |
| 8091 | vLLM-Omni Qwen3-TTS | Streaming / sentence TTS |

## Data flow

1. Browser opens session → Orchestrator.
2. DeepSeek streams tokens → UI `assistant_delta` + TTS provider.
3. TTS emits PCM → `POST /v1/avatar/{id}/audio`.
4. Gateway waits `FACE_PREROLL_MS` (default 1400) then runs Engine jobs.
5. Engine produces ~1.4s MP4 slices; Gateway remuxes fMP4 and pushes `av_mp4` SSE.
6. Browser MSE appends segments (`PREROLL=1`, must start at chunk=1).

## Streaming honesty

| Stage | Class |
|-------|--------|
| LLM | True stream (token SSE) |
| TTS | Semi-stream (sentence `input.done` on current Omni build; in-sentence PCM chunks) |
| Face engine | Batch slices (~1.4s) |
| Playback | Semi-stream MSE segments |

Public path is **HTTP MSE**, not WebRTC (AutoDL HTTPS portals lack reliable UDP/ICE).

## Extensibility boundary

`app/` must talk to avatar runtimes **only** via Gateway HTTP:

- `POST /v1/avatar/session`
- `POST /v1/avatar/{id}/audio` (+ `?end=1`)
- `POST /v1/avatar/{id}/interrupt`
- `GET /v1/avatar/{id}/av/sse`
- `GET /v1/avatar/{id}/mp4/{chunk}`

Swap FlashHead later by implementing the same contract under a new `avatar_backend`.
