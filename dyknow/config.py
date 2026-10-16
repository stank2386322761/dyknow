"""配置：一个 JSON 文件 + 环境变量覆盖。

数据目录（登录用的浏览器目录、配置、日志、模型）放在用户目录下，
不放进项目文件夹，也不放进同步盘，避免被同步软件锁住或复制。
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path


def default_home() -> Path:
    override = os.environ.get("DYKNOW_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "dyknow"


# 每个配置项：默认值 + 一句说明（config 命令会显示）
DEFAULTS: dict[str, tuple[object, str]] = {
    "output_dir": (str(Path.home() / "抖音知识库"), "笔记保存目录"),
    "asr": ("auto", "转录方式：auto / local（本机显卡或CPU）/ cloud（硅基流动）/ none"),
    "model": ("auto", "本地模型：auto（有显卡用 large-v3-turbo，否则 small）或模型名"),
    "device": ("auto", "本地转录设备：auto / cuda / cpu"),
    "language": ("auto", "转录语言：auto（自动识别，中英混合也行）/ zh / en"),
    "siliconflow_api_key": ("", "云端转录的 API Key（asr=cloud 时需要）"),
    "template": ("general", "AI 整理模板：general / creator / 自定义模板文件路径"),
    "comments": (5, "每条视频附带的热门评论数，0 = 不抓"),
    "niche": ("", "我的账号方向，比如\"AI 工具和自媒体变现\"，AI 挑选题时会参考"),
    "frames": (0, "每条视频截取多少张画面（给能看图的 AI 用），0 = 不截；建议 8"),
    "update_limit": (200, "update 最多检查多少条最新收藏"),
    "browser": ("auto", "浏览器：auto（依次尝试 Chrome、Edge、内置 Chromium）/ chrome / msedge / chromium"),
    "headless": (False, "抓取时是否隐藏浏览器窗口（抖音可能拦截隐藏窗口，默认不隐藏）"),
    "request_interval": (1.5, "两次请求之间的间隔秒数，太小容易被限流"),
    "hf_mirror": (True, "下载模型时使用国内镜像 hf-mirror.com"),
}

ENV_OVERRIDES = {
    "output_dir": "DYKNOW_OUTPUT",
    "asr": "DYKNOW_ASR",
    "model": "DYKNOW_MODEL",
    "device": "DYKNOW_DEVICE",
    "siliconflow_api_key": "SILICONFLOW_API_KEY",
}


@dataclass
class Config:
    home: Path = field(default_factory=default_home)
    values: dict = field(default_factory=dict)
    # 测试用：替换抖音站点地址
    base_url: str = "https://www.douyin.com"
    # 测试用：替换其他平台地址，如 {"xhs.www": "http://127.0.0.1:1234"}
    site_urls: dict = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.home / "config.json"

    @property
    def profile_dir(self) -> Path:
        return self.home / "browser-profile"

    @property
    def cache_dir(self) -> Path:
        return self.home / "cache"

    @property
    def models_dir(self) -> Path:
        return self.home / "models"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def output_dir(self) -> Path:
        return Path(str(self.get("output_dir"))).expanduser()

    def get(self, key: str):
        if key not in DEFAULTS:
            raise KeyError(key)
        env = ENV_OVERRIDES.get(key)
        if env and os.environ.get(env, "").strip():
            return _coerce(key, os.environ[env].strip())
        if key in self.values:
            return self.values[key]
        return DEFAULTS[key][0]

    def source_of(self, key: str) -> str:
        env = ENV_OVERRIDES.get(key)
        if env and os.environ.get(env, "").strip():
            return f"环境变量 {env}"
        if key in self.values:
            return "配置文件"
        return "默认值"

    def set(self, key: str, raw) -> object:
        if key not in DEFAULTS:
            raise KeyError(key)
        value = _coerce(key, raw)
        if key == "output_dir":
            p = Path(str(value)).expanduser()
            value = str(p if p.is_absolute() else (Path.cwd() / p).resolve())
        self.values[key] = value
        self.save()
        return value

    def save(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


def _coerce(key: str, raw):
    default = DEFAULTS[key][0]
    if isinstance(default, bool):
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on", "是")
    if isinstance(default, int) and not isinstance(default, bool):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return str(raw)


def load(home: Path | None = None) -> Config:
    cfg = Config(home=home or default_home())
    try:
        data = json.loads(cfg.path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cfg.values = {k: v for k, v in data.items() if k in DEFAULTS}
    except (OSError, ValueError):
        pass
    return cfg
