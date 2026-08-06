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

### 主路径 vs `app/scripts`

**生产 / 开源默认只用 [`deploy/autodl/`](deploy/autodl/)**（`install.sh` → `start_all.sh` → `healthcheck.sh`）。  
`app/scripts/*`（rsync / tunnel / Mac 本地 start / e2e）是 **optional / Mac debug**，不是一键部署入口；日常贡献与冷机验收请勿把它们当成主路径。SSH 辅助脚本统一读 `DREAM_GIRL_SSH_ENV`（见 [CONTRIBUTING.md](CONTRIBUTING.md)）。

## License

Apache-2.0 for this repository's orchestration and deploy scripts. Third-party models/runtimes: see [NOTICE](NOTICE).

### License checklist（商用前必读）

本仓编排代码可按 Apache-2.0 使用；**能否商用取决于你实际拉取/调用的上游权重与 API**，须自行核对原文。约 2 分钟自查：

| # | 组件 | 你要核对什么 | 入口 |
|---|------|--------------|------|
| 1 | **Qwen3-TTS**（默认本地 TTS） | 模型卡 / LICENSE 是否允许你的商用场景；权重由 `install`/`download` 脚本拉取，**不随本仓分发** | [HF Qwen3-TTS-0.6B-CustomVoice](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice) · [ModelScope](https://modelscope.cn/models/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice) |
| 2 | **SoulX-FlashHead**（数字人） | 代码仓与 `Model_Pro` 权重许可；是否允许再分发 / 商用产品嵌入 | [SoulX-FlashHead 代码](https://github.com/Soul-AILab/SoulX-FlashHead) · [权重 HF](https://huggingface.co/Soul-AILab/SoulX-FlashHead-1_3B) |
| 3 | **vLLM / vLLM-Omni** | 运行时许可证（通常 Apache-2.0）与依赖条款 | [vLLM](https://github.com/vllm-project/vllm) · [vLLM-Omni](https://github.com/vllm-project/vllm-omni) |
| 4 | **自查** | DeepSeek / MiniMax 等云 API 的 ToS；`assets/character/` 仅为 demo 立绘，**生产必须换成自有授权素材** | [NOTICE](NOTICE) · [assets/character/README.md](assets/character/README.md) |

**一句话决策**：本仓 ≠ 上游商用授权。未读完上表对应链接前，不要假设可上线收费产品。完整第三方说明见 [NOTICE](NOTICE)。
