# preset_clips（V2 底片）

## 现状

| 文件 | 说明 |
|------|------|
| `xiaoya_idle_512x768.mp4` | **占位**：立绘 2s 呼吸循环（非 Seedance） |
| `xiaoya_idle_512x768.boxes.jsonl` | 预计算脸 box |
| `xiaoya_face_ref_256.jpg` | FlashHead 脸参考 |
| `manifest.json` | Gateway 随机抽片清单 |
| `seedance_prompts.yaml` | 4×15s Seedance 提示词 |

## 用 Dreamina Seedance 2.0 生成 4×15s

需要 `FAL_KEY`（fal.ai 上的 Seedance 2.0）：

```bash
cd lightning-FlashHead
pip install fal-client pyyaml
export FAL_KEY=你的key

python scripts/generate_seedance_presets.py \
  --image ../../../demo/assets/character/xiaoya-v1.jpg

# 只看提示词
python scripts/generate_seedance_presets.py --dry-run
```

脚本会：

1. 调 `bytedance/seedance-2.0/image-to-video` 出 4 段约 15s（3:4）
2. ffmpeg 裁到 **512×768 @20fps**
3. `extract_face_boxes.py` 写 `*.boxes.jsonl`
4. 更新 `manifest.json`

也可 Dreamina 网页手搓 4 段，命名为：

- `xiaoya_seedance_sway_a_512x768.mp4`
- `xiaoya_seedance_sway_b_512x768.mp4`
- `xiaoya_seedance_hair_hand_512x768.mp4`
- `xiaoya_seedance_think_idle_512x768.mp4`

再跑抽框并手写/更新 `manifest.json`。

会话创建时 Gateway **随机抽一段**底片循环，FlashHead 只贴脸。
