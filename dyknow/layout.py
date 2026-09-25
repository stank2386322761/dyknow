"""
知识库目录语义与文件命名常量

v1 用单个「未分类/」同时承担「尚未处理」和「已处理但分类数量不足」两种含义，
导致 AI 无法判断一条笔记到底还需不需要处理。

v2 拆开：

    未处理/    = 尚未完成 AI 知识处理（transcript 维度尚未就绪，或 processing 尚未完成）
    待聚类/    = 已完成 AI 处理，但当前没有足够稳定的分类归属

旧目录「未分类/」保留读取兼容，但不自动批量迁移（迁移必须显式执行）。
"""

from __future__ import annotations

from pathlib import Path

# ── 目录名 ────────────────────────────────────

UNPROCESSED_DIR = "未处理"
LEGACY_UNCLASSIFIED_DIR = "未分类"
PENDING_CLUSTER_DIR = "待聚类"

# 读取时视为「未处理」的等价目录
UNPROCESSED_DIR_ALIASES = (UNPROCESSED_DIR, LEGACY_UNCLASSIFIED_DIR)

# ── 文件名 ────────────────────────────────────

INDEX_FILENAME = "知识库索引.md"
CATEGORY_INDEX_FILENAME = "索引.md"
TAXONOMY_FILENAME = "taxonomy.yaml"
TAXONOMY_JSON_FILENAME = "taxonomy.json"

# 由程序生成的索引文件，不得被当作视频笔记统计
INDEX_FILENAMES = {INDEX_FILENAME, CATEGORY_INDEX_FILENAME}


def is_index_file(path: Path) -> bool:
    """
    判断某个 Markdown 文件是否为程序生成的索引。

    索引文件没有 frontmatter 中的 source_id / video_id，
    同时也按文件名兜底。
    """
    return Path(path).name in INDEX_FILENAMES


def is_unprocessed_dir(name: str) -> bool:
    """目录名是否表示「未处理」（含旧「未分类」）"""
    return name in UNPROCESSED_DIR_ALIASES


def unprocessed_dirs(root: Path) -> list[Path]:
    """列出知识库中所有「未处理」等价目录"""
    root = Path(root)
    return [root / n for n in UNPROCESSED_DIR_ALIASES if (root / n).is_dir()]


def knowledge_root(output_dir: Path) -> Path:
    """
    知识库根目录。

    约定：输出目录即知识库根目录——「未处理/」「待聚类/」「taxonomy.yaml」
    与分类目录都在同一层，便于目录语义统一。
    """
    return Path(output_dir)
