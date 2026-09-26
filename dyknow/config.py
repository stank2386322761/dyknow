"""
配置管理模块

笔记输出目录的解析优先级（高 → 低）：

    1. 运行时覆盖（CLI `--output` / 调用方直接赋值）
    2. `DYKNOW_OUTPUT_DIR` 环境变量
    3. `data/config.json` 里保存的 output_dir（首次使用写入，之后长期记住）
    4. `<当前工作目录>/抖音收藏`

第 3 条是本模块的核心：SKILL.md 要求「首次确认一次输出目录，后续都用它」，
仅靠环境变量做不到跨会话记住，因此提供最小的持久化配置文件。

配置文件放在 data_dir 下，而 `data/` 已在 .gitignore 中，不会进入版本库。
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILENAME = "config.json"


@dataclass
class Config:
    """全局配置，支持环境变量与配置文件覆盖"""

    # 数据目录（cookie、数据库、视频缓存、配置文件）
    data_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("DYKNOW_DATA_DIR", Path(__file__).parent.parent / "data")
    ))

    # 默认同步数量
    default_count: int = 200

    # 是否保留临时缓存文件（视频/音频）
    # 某些运行环境对批量删除有限制（例如每轮最多删 N 个文件），
    # 大批量转录时会把进程拦停。置为 True 可由调用方在结束后统一清理。
    keep_media_cache: bool = os.environ.get(
        "DYKNOW_KEEP_MEDIA_CACHE", ""
    ).strip().lower() in ("1", "true", "yes")

    # 转录时每条之间的随机休息上限（秒），用于降低被限流概率。
    # 设为 0 则不休息。通过 DYKNOW_TRANSCRIBE_DELAY 设置。
    transcribe_delay: float = float(
        os.environ.get("DYKNOW_TRANSCRIBE_DELAY", "0") or "0"
    )

    # AI 摘要后端: "claude" | "openai" | "ollama" | None
    summary_backend: str | None = os.environ.get("DYKNOW_SUMMARY_BACKEND", None)
    summary_model: str = os.environ.get("DYKNOW_SUMMARY_MODEL", "claude-sonnet-4-6")

    # 转录模型 — pywhispercpp (whisper.cpp GGML)
    # 默认使用 ggml-tiny.bin (74MB)，可切换为 ggml-small.bin (465MB)
    ggml_model_path: str = os.environ.get(
        "DYKNOW_GGML_MODEL",
        str(Path(__file__).parent.parent / "data" / "models" / "ggml-tiny.bin"),
    )

    def __post_init__(self):
        # 运行时覆盖（--output）。不作为 dataclass 字段，避免污染 __init__ 签名。
        self._output_override: Path | None = None

    # ── 派生路径 ──────────────────────────────

    @property
    def cookie_path(self) -> Path:
        """Cookie 文件路径"""
        return self.data_dir / "cookies.json"

    @property
    def db_path(self) -> Path:
        """SQLite 数据库路径"""
        return self.data_dir / "dyknow.db"

    @property
    def video_cache_dir(self) -> Path:
        """视频缓存目录（转录后删除）"""
        p = self.data_dir / "videos"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def config_path(self) -> Path:
        """持久化配置文件路径"""
        return self.data_dir / CONFIG_FILENAME

    # ── 配置文件读写 ──────────────────────────

    def read_config_file(self) -> dict:
        """读取配置文件。不存在或内容损坏时返回空 dict，绝不抛异常。"""
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_output_dir(self, path) -> Path:
        """
        把笔记输出目录写入配置文件，之后每次运行都会记住。

        传入相对路径时按当前工作目录展开为绝对路径（避免日后 cwd 变化导致漂移）。
        返回规范化后的路径。
        """
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = Path.cwd() / target
        target = Path(os.path.normpath(str(target)))

        data = self.read_config_file()
        data["output_dir"] = str(target)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return target

    # ── 输出目录 ──────────────────────────────

    @property
    def output_dir(self) -> Path:
        """
        笔记输出目录。

        优先级：运行时覆盖 > DYKNOW_OUTPUT_DIR > data/config.json > cwd/抖音收藏
        """
        if self._output_override is not None:
            return self._output_override

        env = (os.environ.get("DYKNOW_OUTPUT_DIR") or "").strip()
        if env:
            return Path(env)

        saved = str(self.read_config_file().get("output_dir") or "").strip()
        if saved:
            return Path(saved)

        return Path.cwd() / "抖音收藏"

    @output_dir.setter
    def output_dir(self, value) -> None:
        """运行时覆盖输出目录（不写盘）。传 None 表示清除覆盖。"""
        self._output_override = Path(value).expanduser() if value else None

    def output_dir_source(self) -> str:
        """说明当前输出目录来自哪一级，便于 status / config --show 展示"""
        if self._output_override is not None:
            return "运行时覆盖（--output）"
        if (os.environ.get("DYKNOW_OUTPUT_DIR") or "").strip():
            return "环境变量 DYKNOW_OUTPUT_DIR"
        if str(self.read_config_file().get("output_dir") or "").strip():
            return f"配置文件 {self.config_path}"
        return "默认值（当前工作目录）"

    def ensure_dirs(self):
        """确保所有需要的目录存在"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.video_cache_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)


# 全局单例
config = Config()
