# WebRTC：好不好用、优势、我们为什么用不了

## 结论（2026-08 更新）

WebRTC **本身很好用**，尤其适合「连续低延迟音画」。

但在当前 **浏览器 → AutoDL HTTPS 反代** 链路上，**媒体面（UDP/ICE）经常穿不过**，且 FlashHead Gateway **没有**持续 RTP 推流端。

公网 demo **现行唯一主路径**：

- **MSE + fMP4**（`av_mode: mse_fmp4`，前端 [`web/ui/av-mse.js`](../web/ui/av-mse.js)）
- SSE 推段元数据 → GET fMP4 → `SourceBuffer.mode=sequence` 追加成**一条时间轴**
- **不再**使用双 `video` 接力，也**不做**双 video 回退

**WebRTC**：仅保留为「内网 / 已部署 TURN + 另建 RTP 推流」时的可选高速通道，**当前不做、不作为依赖**。

## WebRTC 优势（相对短 MP4 接力）

| 点 | 说明 |
|----|------|
| 无段缝 | 一条连续媒体时间轴，无双 `video` 切源 |
| 低延迟 | 编码后 RTP 直推，少「写完整 MP4 → GET → load/play」 |
| 音画同步 | 浏览器 `MediaStream` 原生对齐 |
| 自适应 | 可按网络调码率/丢包；HTTP 短片则是「段到了才播」 |

数字人实时说话场景，行业常见优解就是 WebRTC（或同类实时协议）。**MSE/fMP4 是在「不能上 WebRTC」时，用 HTTPS 尽量逼近「连续时间轴」的方案。**

## 为什么 AutoDL 公网做不了 WebRTC 连续轨

```text
Browser --HTTPS_OK--> Orchestrator --HTTP_OK--> Gateway
Browser -.- UDP/ICE 常失败 -.- > GPU Host
Browser -.- 需要 TURN 中继 -.- > Relay
```

1. 公网入口偏 HTTP(S)；媒体面要 UDP/ICE 或 TCP TURN。  
2. 只有 STUN、没有 TURN → 对称 NAT / 只放行 443 易失败。  
3. FlashHead 交付的是 **分段 fMP4**，不是持续 WebRTC 轨；要 WebRTC 需另建「持续编码 → RTP」。  
4. 信令通 ≠ 媒体通。

要强上 WebRTC：TURN（443）+ Gateway 持续推流，工作量明显高于把 MSE/fMP4 做稳。

## 决策（已采纳）

1. 公网 demo **主路径**：MSE + fMP4（消段缝）。  
2. WebRTC **本期不做**；代码可保留供内网联调。  
3. 调试可设 `FACE_MSE_FORMAT=mp4` 回退 progressive（不推荐线上）。
