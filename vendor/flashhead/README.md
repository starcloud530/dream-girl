# lightning-FlashHead

单卡 RTX 5090 · SoulX-FlashHead **Model_Pro** 实时说话头像加速壳。

详见：

- [docs/v2/实现方案.md](../docs/v2/实现方案.md) — **默认**：预置 512×768 + FlashHead 256 脸区贴回
- [docs/v1/实现方案.md](../docs/v1/实现方案.md) — 整幅 464² 对照档

## 快速开始（AutoDL）

```bash
# 本机
bash scripts/rsync_to_autodl.sh

# 远端
cd /root/autodl-tmp/lightning-FlashHead
bash scripts/setup_env.sh
bash scripts/download_weights.sh
# 建议: pip install sageattention（源码）
bash scripts/run_bench.sh configs/t_v2_face256.yaml
# 双进程（推荐）：Engine :6009 常驻模型 + Gateway :6008 薄层
bash scripts/start_stack_autodl.sh
# 改 gateway 代码后只需：bash scripts/start_gateway_autodl.sh（不卸模型）
```

默认生产配置：`configs/t_v2_face256.yaml`（脸 256² @ 20fps + 贴回 512×768）。  
V1 对照：`FLASHHEAD_CONFIG=configs/t1_compile.yaml`。

---

## 本地 Qwen3-TTS（:6010）

在 **同一张 5090** 上并行跑 Qwen3-TTS CustomVoice（内置 speaker，如 Serena），替代云端 MiniMax：

```bash
# 1) 下载权重（ModelScope 直链优先，失败回退 HF 镜像）
bash scripts/download_qwen_tts.sh
# 2) 一键 bench：TTFA / RTF / 显存
bash scripts/run_bench.sh configs/t1_384_x2.yaml 2>/dev/null; \
  "${PYTHON_BIN}" scripts/bench_qwen_tts.py -c configs/qwen_tts.yaml
# 3) 起服务 :6010（前台调试）
bash scripts/serve_tts.sh
#    或 AutoDL 后台
bash scripts/start_tts_autodl.sh
# 4) 演示编排器切到本地 TTS：demo/config/app.autodl.yaml → tts.provider: "qwen"
```

配置：`configs/qwen_tts.yaml`（speaker / language / instruct / sample_rate）。

> 前置依赖：`pip install qwen-tts modelscope`；建议 `flash-attn`。  
> 音频输出 16k PCM，与 FlashHead 输入对齐；HTTP/WS 协议见 `serve/tts_server.py`。
