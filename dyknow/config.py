"""
配置管理模块
"""

import os
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class Config:
    """全局配置，支持环境变量覆盖"""

    # 数据目录（cookie、数据库、视频缓存）
    data_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("DYKNOW_DATA_DIR", Path(__file__).parent.parent / "data")
    ))

    # 笔记输出目录
    output_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("DYKNOW_OUTPUT_DIR", Path.cwd() / "抖音收藏")
    ))

    # Cookie 文件路径
    @property
    def cookie_path(self) -> Path:
        return self.data_dir / "cookies.json"

    # SQLite 数据库路径
    @property
    def db_path(self) -> Path:
        return self.data_dir / "dyknow.db"

    # 视频缓存目录（转录后删除）
    @property
    def video_cache_dir(self) -> Path:
        p = self.data_dir / "videos"
        p.mkdir(parents=True, exist_ok=True)
        return p

    # 默认同步数量
    default_count: int = 200

    # AI 摘要后端: "claude" | "openai" | "ollama" | None
    summary_backend: str | None = os.environ.get("DYKNOW_SUMMARY_BACKEND", None)
    summary_model: str = os.environ.get("DYKNOW_SUMMARY_MODEL", "claude-sonnet-4-6")

    # 转录模型 — pywhispercpp (whisper.cpp GGML)
    # 默认使用 ggml-tiny.bin (74MB)，可切换为 ggml-small.bin (465MB)
    ggml_model_path: str = os.environ.get(
        "DYKNOW_GGML_MODEL",
        str(Path(__file__).parent.parent / "data" / "models" / "ggml-tiny.bin"),
    )

    def ensure_dirs(self):
        """确保所有需要的目录存在"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.video_cache_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)


# 全局单例
config = Config()
