# AGENTS.md — Dream Girl

给 **Cursor / 子 Agent** 的作业手册：如何在本仓库改代码、推进 GitHub Issue，并在验收通过后**关闭** Issue。

人类贡献者请同时看 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [README.md](README.md)。

---

## 1. 项目一句话

单卡近实时数字人 MVP：`Orchestrator(:6006)` + `Qwen-TTS/vLLM-Omni(:8091)` + `FlashHead GW(:6008)/Engine(:6009)`，公网主路径是 **MSE + fMP4**（不是 WebRTC）。

仓库：https://github.com/starcloud530/dream-girl  
本地根目录：本文件所在目录（`dream-girl/`）。

---

## 2. 开工前必读边界

| 规则 | 说明 |
|------|------|
| 主卖点 | 一键部署 `deploy/autodl/`；改启动顺序/端口必须同步 README + docs |
| Vendor 隔离 | `app/` **禁止** `import vendor.flashhead`；只走 HTTP Avatar 契约 |
| 可插拔 | 换 LLM/TTS/Avatar = 新 Provider + yaml，不改 Web MSE 主路径 |
| 不进 git | `.env`、密钥、`.pth`/`.safetensors`、大音视频、`tmp/` |
| 不硬编码 | AutoDL 实例 URL、固定 SSH 端口、本机 `/Users/...` 绝对路径 |
| 历史洁癖 | 公开仓应为**干净单提交史**；发现泄露不要「只改 tip」，要报告维护者做 history purge |
| MVP 不做 | 多租户、WebRTC 主路径、EchoMimic 代码/权重入仓、上传模型二进制 |

权威文档：

- 架构 / 端口：[docs/architecture.md](docs/architecture.md)
- Provider：[docs/providers.md](docs/providers.md)
- 硬件：[docs/hardware.md](docs/hardware.md)
- Avatar OpenAPI：`app/contracts/openapi/avatar.yaml`
- 示例配置：`configs/app.example.yaml`、`.env.example`

---

## 3. 目录地图（改哪里）

```text
deploy/autodl/     ← install / start_all / stop / healthcheck（开源默认入口）
configs/           ← 脱敏样例配置
app/               ← Orchestrator、providers、web、contracts
vendor/flashhead/  ← Engine+Gateway（仅 HTTP 被 app 调用）
docs/              ← 对外说明
assets/character/  ←  canonical 立绘；install/start 会 rsync 到 app/assets
```

`app/scripts/*`（rsync/tunnel/e2e）是 **可选/调试**，不是一键主路径；改它们前先确认 issue 是否要求动主路径。

---

## 4. Issue 工作流（更新 → 关闭）

### 4.1 选任务

```bash
gh issue list -R starcloud530/dream-girl --state open --limit 30
```

优先级：**P0 → P1 → P2**（看标题 `[P0]`/`[P1]`/`[P2]` 或 label）。  
一次只认领 **一个** issue，除非用户明确要求并行。

### 4.2 认领与进度评论

```bash
# 开始时留言（可 @ 自己或写 Assignees）
gh issue comment <N> -R starcloud530/dream-girl --body "$(cat <<'EOF'
## Agent 开工
- 目标：对照本 issue 验收标准实施
- 分支/工作区：本地 dream-girl/
- 预计改动范围：<paths>
EOF
)"

# 可选：把自己加到 assignees（需有权限）
gh issue edit <N> -R starcloud530/dream-girl --add-assignee @me
```

中途若阻塞（缺 GPU、缺密钥、上游许可不明）：

```bash
gh issue comment <N> -R starcloud530/dream-girl --body "$(cat <<'EOF'
## Blocked
- 原因：...
- 需要人类：...
- 已尝试：...
EOF
)"
```

**Blocked 时不要 close。**

### 4.3 实施与自检

1. 只改与该 issue 相关的文件；避免顺手大重构。
2. 对照 issue body 里的 checklist，逐项打勾（在关闭评论里复述）。
3. 安全自检（改脚本/文档后必跑）：

```bash
# 工作区与即将提交的内容
rg -n 'sk-[a-zA-Z0-9]{20,}|785020[0-9]+|uu[0-9]+-[0-9]+\.|PORT=53[0-9]{3}|/Users/AI|/Users/jiang' \
  --glob '!.git/**' . || true

# 确认没有权重进暂存
git status -sb
git diff --cached --stat
```

4. 若动了 `deploy/autodl/*.sh`：`bash -n deploy/autodl/*.sh`
5. 若动了端口/Provider/启动顺序：更新 README + 对应 `docs/*`

### 4.4 提交（仅当用户要求 commit，或用户说「做完并提交」）

```bash
git add <相关文件>
git commit -m "$(cat <<'EOF'
<type>: <why，对应 issue #N>

EOF
)"
# type: fix | feat | docs | chore | deploy
# 需要时再：git push origin HEAD
```

**不要** `--force` 推 `main`，除非维护者明确要求做 history rewrite。  
**不要** 把 `.env`、密钥、权重加进 commit。

### 4.5 关闭 Issue（验收通过才关）

关闭前评论必须包含：**做了什么、如何验证、checklist 结果**。

```bash
gh issue comment <N> -R starcloud530/dream-girl --body "$(cat <<'EOF'
## Done
### 变更
- <file>: <一句话>

### 验收对照
- [x] <issue 里的标准 1>
- [x] <标准 2>
- [ ] <未做且说明为何非阻塞 / 或拆到新 issue>

### 验证命令 / 证据
\`\`\`bash
# 实际跑过的命令与关键输出摘要
\`\`\`

### 后续（可选）
- 关联 commit / PR：...
EOF
)"

gh issue close <N> -R starcloud530/dream-girl --reason completed
```

若只完成一半：

```bash
gh issue comment <N> ...  # 写清剩余项
# 不要 close；或拆：
gh issue create -R starcloud530/dream-girl --title "[P?] 剩余：..." --body "从 #<N> 拆出：..."
```

错误关闭可重开：

```bash
gh issue reopen <N> -R starcloud530/dream-girl
```

---

## 5. 按 Issue 类型的「完成定义」

### Deploy / 一键（如 #1 #2 #8）

- `install.sh` / `start_all.sh` / `healthcheck.sh` /（若有）`download_weights.sh` 行为与 README 一致
- 默认 `tts.provider=qwen` 时 Omni 失败必须 **fail-fast**
- 权重只下载到 `DREAM_GIRL_MODELS_ROOT`，不进 git
- 冷机验收类 issue：没有真实跑通证据就 **不要 close**（可 comment + 留 open，或标 blocked）

### Docs / License（如 #3 #4 #6 #9）

- 读者 2 分钟能按文档做决策（商用自查 / 选卡 / 主路径 vs 调试路径）
- 中英文混排时以现有 README 语言风格为准；issue 标题中文可保留

### Provider / OpenAPI（如 #5 #7 #11）

- 保持 HTTP 契约；禁止 app 直引 flashhead 内部模块
- OpenAPI 与 `vendor/flashhead/serve/gateway.py` 路由一致
- 第二 Avatar 后端：**仅文档契约**，不塞 EchoMimic 代码

### CI（如 #10）

- 无 GPU integration；可做 `bash -n`、密钥/路径禁扫、轻量 lint
- 不引入会泄露实例指纹的测试 fixture

---

## 6. 常用命令速查

```bash
# Issue
gh issue list -R starcloud530/dream-girl --label P0
gh issue view <N> -R starcloud530/dream-girl
gh issue close <N> -R starcloud530/dream-girl --reason completed

# 本地一键（在 GPU 机器上）
cp -n .env.example .env   # 填 DEEPSEEK_API_KEY
bash deploy/autodl/install.sh
bash deploy/autodl/start_all.sh
bash deploy/autodl/healthcheck.sh
bash deploy/autodl/stop.sh
```

端口：`6006` Orch+Web · `6008` Gateway · `6009` Engine · `8091` Omni TTS。

---

## 7. 子 Agent 回复用户时的习惯

- 用中文简洁汇报：改了什么、验证了什么、关闭了哪个 issue
- 贴 issue / commit 链接
- 未关闭时说明 blocker，不要假装完成
- **不要编辑** 用户的 plan 文件；**不要** 无请求地改 git config / force push

---

## 8. 当前开放 Issue 索引（会过期；以 `gh issue list` 为准）

| # | 优先级 | 主题 |
|---|--------|------|
| 1 | P0 | 冷机一键安装验收 |
| 2 | P0 | download-weights 独立分层 |
| 3 | P0 | README/NOTICE 许可自查清单 |
| 4 | P1 | 4090 / 降配实测 |
| 5 | P1 | Provider 可插拔示例 |
| 6 | P1 | 清理 app/scripts 文档入口 |
| 7 | P1 | Avatar OpenAPI 对齐 |
| 8 | P2 | Docker GPU 骨架 |
| 9 | P2 | Mac debug 文档 |
| 10 | P2 | CI 密钥扫描 + bash -n |
| 11 | P2 | 第二 Avatar 后端文档契约 |

开工时先 `gh issue list`，上表可能已过时。
