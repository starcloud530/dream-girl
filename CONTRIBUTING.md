# Contributing to Dream Girl

Thanks for helping. This project prioritizes **one-click deployability** and
**swappable providers** (LLM / TTS / Avatar backend).

## Ground rules

1. Do not commit secrets (`.env`, API keys, AutoDL passwords).
2. Do not commit model weights (`.pth`, `.safetensors`, large checkpoints).
3. Keep `app/` talking to avatar runtimes only via HTTP Gateway contracts.
4. Prefer config + new Provider modules over rewriting the Web client.

## Dev loop

```bash
# App deps (Mac / Linux)
cd app && pip install -e .

# Docs-only PRs are welcome for deploy clarity.
```

## Deploy entry vs `app/scripts`

- **Default (GPU / production / OSS):** only [`deploy/autodl/`](deploy/autodl/) — `install.sh`, `start_all.sh`, `healthcheck.sh`, `stop.sh`.
- **`app/scripts/*`:** optional / Mac debug helpers (local orchestrator, e2e smoke, rsync, SSH tunnel). They are **not** the one-click path; do not document or CI them as the primary entry.

### SSH env for optional helpers

`rsync_to_autodl.sh` and `tunnel_autodl.sh` read a local env file (must define `HOST=` / `PORT=` / optional `PASS=`):

```bash
export DREAM_GIRL_SSH_ENV=/path/to/ssh.env
```

`CYBER_GF_SSH_ENV` remains a **deprecated alias** for the same file path (compat only; new docs and shells should use `DREAM_GIRL_SSH_ENV`).

## PR checklist

- [ ] Works with `configs/app.example.yaml` + `.env.example`
- [ ] Docs updated if ports / providers / start order changed
- [ ] No absolute machine-specific paths without env overrides
- [ ] Do not present `app/scripts` as the production deploy entry
