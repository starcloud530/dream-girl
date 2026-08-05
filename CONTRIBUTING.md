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

## PR checklist

- [ ] Works with `configs/app.example.yaml` + `.env.example`
- [ ] Docs updated if ports / providers / start order changed
- [ ] No absolute machine-specific paths without env overrides
