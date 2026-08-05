from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# app/ is repo root for Dream Girl layout (was demo/ in monorepo)
_REPO_ROOT = Path(__file__).resolve().parents[1]
# Prefer dream-girl repo root .env; fall back to legacy sibling .env/
_DG_ROOT = Path(os.environ.get("DREAM_GIRL_ROOT", str(_REPO_ROOT.parent))).expanduser()
_ENV_DIR = Path(os.environ.get("DREAM_GIRL_ENV_DIR", str(_DG_ROOT / ".env"))).expanduser()
if not _ENV_DIR.exists():
    _ENV_DIR = _REPO_ROOT.parent / ".env"


def _expand(value: str) -> str:
    return os.path.expandvars(os.path.expanduser(value or ""))


@dataclass
class DeepSeekConfig:
    api_key: str
    base_url: str
    model_id: str
    temperature: float = 0.9
    max_tokens: int = 8192
    timeout: float = 60.0


@dataclass
class MiniMaxConfig:
    api_key: str
    group_id: str = ""
    ws_url: str = "wss://api.minimaxi.com/ws/v1/t2a_v2"
    http_base: str = "https://api.minimaxi.com/v1"
    model: str = "speech-2.6-turbo"
    voice_id: str = "female-tianmei"
    speed: float = 1.08
    vol: float = 1.2
    pitch: int = 0


@dataclass
class EdgeTTSConfig:
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: str = "+0%"


@dataclass
class QwenTTSConfig:
    base_url: str = "http://127.0.0.1:8091"
    ws_url: str = "ws://127.0.0.1:8091/v1/audio/speech/stream"
    speaker: str = "Serena"
    language: str = "Chinese"
    instruct: str = "温柔亲切，适合日常对话"
    task_type: str = "CustomVoice"
    native_sample_rate: int = 24000
    model: str = ""


@dataclass
class AppConfig:
    system_prompt: str
    orchestrator_host: str
    orchestrator_port: int
    avatar_public_url: str
    livetalking_base_url: str
    livetalking_avatar_id: str
    avatar_mode: str
    avatar_backend: str
    tts_provider: str
    deepseek: DeepSeekConfig | None
    minimax: MiniMaxConfig | None
    edge_tts: EdgeTTSConfig
    qwen_tts: QwenTTSConfig
    sentence_min_chars: int
    sentence_max_chars: int
    sentence_delimiters: str
    portrait_local_path: str = "assets/character/xiaoya-v1.jpg"
    portrait_cache_url: str = ""
    portrait_aspect_ratio: str = "480:768"
    video_width: int = 480
    video_height: int = 768
    longcat_resolution: str = "480p"
    pcm_pacer_enabled: bool = True
    pcm_pacer_preroll_ms: int = 600
    pcm_pacer_quantum_ms: int = 100


def _parse_yaml_block(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    # deepseek.env uses nested yaml-like block under a key
    if "api_key:" in text and path.name == "deepseek.env":
        # strip first line key if present
        lines = text.strip().splitlines()
        if lines and lines[0].endswith(":"):
            text = "\n".join(lines[1:])
    return yaml.safe_load(text) or {}


def load_config() -> AppConfig:
    cfg_override = os.environ.get("DREAM_GIRL_APP_CONFIG") or os.environ.get(
        "CYBER_GF_APP_CONFIG"
    )
    app_path = (
        Path(_expand(cfg_override))
        if cfg_override
        else _REPO_ROOT / "config" / "app.yaml"
    )
    raw_yaml = app_path.read_text(encoding="utf-8")
    models_root = os.environ.get("DREAM_GIRL_MODELS_ROOT", "/root/autodl-fs/models")
    raw_yaml = raw_yaml.replace("${DREAM_GIRL_MODELS_ROOT}", models_root)
    app = yaml.safe_load(raw_yaml)
    tts = app.get("tts", {})
    minimax_yaml = tts.get("minimax", {}) or {}

    deepseek_cfg = None
    ds_path = _ENV_DIR / "deepseek.env"
    if ds_path.exists():
        raw = ds_path.read_text(encoding="utf-8")
        block = yaml.safe_load(raw.split(":", 1)[1] if ":" in raw.splitlines()[0] else raw)
        if isinstance(block, dict) and block.get("api_key"):
            deepseek_cfg = DeepSeekConfig(
                api_key=str(block["api_key"]),
                base_url=str(block.get("base_url", "https://api.deepseek.com/v1")),
                model_id=str(block.get("model_id", "deepseek-chat")),
                temperature=float(block.get("temperature", 0.9)),
                max_tokens=int(block.get("max_tokens", 8192)),
                timeout=float(block.get("timeout", 60)),
            )
    # Prefer plain env (open-source friendly)
    if os.environ.get("DEEPSEEK_API_KEY"):
        deepseek_cfg = DeepSeekConfig(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            model_id=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            temperature=float(os.environ.get("DEEPSEEK_TEMPERATURE", "0.9")),
            max_tokens=int(os.environ.get("DEEPSEEK_MAX_TOKENS", "8192")),
            timeout=float(os.environ.get("DEEPSEEK_TIMEOUT", "60")),
        )

    minimax_cfg = None
    mm_path = _ENV_DIR / ".minimax.env"
    if mm_path.exists():
        env_map: dict[str, str] = {}
        for line in mm_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env_map[k.strip()] = v.strip()
        if env_map.get("API_KEY"):
            minimax_cfg = MiniMaxConfig(
                api_key=env_map["API_KEY"],
                group_id=env_map.get("GROUP_ID", env_map.get("group_id", "")),
                ws_url=env_map.get(
                    "WS_URL", "wss://api.minimaxi.com/ws/v1/t2a_v2"
                ),
                http_base=env_map.get(
                    "HTTP_BASE", "https://api.minimaxi.com/v1"
                ),
                model=env_map.get("MODEL", "speech-2.6-turbo"),
                voice_id=env_map.get("VOICE_ID", "female-tianmei"),
                speed=float(env_map.get("SPEED", "1.08")),
                vol=float(env_map.get("VOL", "1.2")),
                pitch=int(env_map.get("PITCH", "0")),
            )
    if os.environ.get("MINIMAX_API_KEY"):
        minimax_cfg = MiniMaxConfig(
            api_key=os.environ["MINIMAX_API_KEY"],
            group_id=os.environ.get("MINIMAX_GROUP_ID", ""),
        )
    if minimax_cfg:
        if minimax_yaml.get("voice_id"):
            minimax_cfg.voice_id = str(minimax_yaml["voice_id"])
        if minimax_yaml.get("model"):
            minimax_cfg.model = str(minimax_yaml["model"])
        if minimax_yaml.get("speed") is not None:
            minimax_cfg.speed = float(minimax_yaml["speed"])
        if minimax_yaml.get("vol") is not None:
            minimax_cfg.vol = float(minimax_yaml["vol"])
        if minimax_yaml.get("pitch") is not None:
            minimax_cfg.pitch = int(minimax_yaml["pitch"])

    sb = app.get("sentence_buffer", {})
    ag = app.get("avatar_gateway", {})
    lt = app.get("livetalking", {})
    orch = app.get("orchestrator", {})
    edge = tts.get("edge", {})
    qwen = tts.get("qwen", {}) or {}
    portrait = app.get("portrait", {}) or {}

    return AppConfig(
        system_prompt=str(app.get("system_prompt", "")).strip(),
        orchestrator_host=str(orch.get("host", "127.0.0.1")),
        orchestrator_port=int(orch.get("port", 8011)),
        avatar_public_url=str(ag.get("public_url", "http://127.0.0.1:8020")),
        livetalking_base_url=str(lt.get("base_url", "http://127.0.0.1:8010")),
        livetalking_avatar_id=str(lt.get("avatar_id", "xiaoya_v1")),
        avatar_mode=str(app.get("avatar_mode", "browser")),
        avatar_backend=str(app.get("avatar_backend", "livetalking")),
        tts_provider=str(tts.get("provider", "auto")),
        deepseek=deepseek_cfg,
        minimax=minimax_cfg,
        edge_tts=EdgeTTSConfig(
            voice=str(edge.get("voice", "zh-CN-XiaoxiaoNeural")),
            rate=str(edge.get("rate", "+0%")),
        ),
        qwen_tts=QwenTTSConfig(
            base_url=str(qwen.get("base_url", "http://127.0.0.1:8091")),
            ws_url=str(
                qwen.get(
                    "ws_url", "ws://127.0.0.1:8091/v1/audio/speech/stream"
                )
            ),
            speaker=str(qwen.get("speaker", "Serena")),
            language=str(qwen.get("language", "Chinese")),
            instruct=str(qwen.get("instruct", "温柔亲切，适合日常对话")),
            task_type=str(qwen.get("task_type", "CustomVoice")),
            native_sample_rate=int(qwen.get("native_sample_rate", 24000)),
            model=_expand(str(qwen.get("model", ""))),
        ),
        sentence_min_chars=int(sb.get("min_chars", 8)),
        sentence_max_chars=int(sb.get("max_chars", 48)),
        sentence_delimiters=str(sb.get("delimiters", "。！？；\n")),
        portrait_local_path=str(
            portrait.get("local_path", "assets/character/xiaoya-v1.jpg")
        ),
        portrait_cache_url=str(portrait.get("cache_url", "")).strip(),
        portrait_aspect_ratio=str(portrait.get("aspect_ratio", "480:768")),
        video_width=int(portrait.get("video_width", 480)),
        video_height=int(portrait.get("video_height", 768)),
        longcat_resolution=str(portrait.get("longcat_resolution", "480p")),
        pcm_pacer_enabled=bool((app.get("pcm_pacer") or {}).get("enabled", True)),
        pcm_pacer_preroll_ms=int((app.get("pcm_pacer") or {}).get("preroll_ms", 600)),
        pcm_pacer_quantum_ms=int((app.get("pcm_pacer") or {}).get("quantum_ms", 100)),
    )
