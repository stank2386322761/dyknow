"""
分类注册表（taxonomy）

v1 的分类规则硬编码在 scripts/organize_notes.py 里（6 大类 26 子类正则表），
且「未分类」同时承担「未处理」和「已处理但分类不足」两种含义；
模型也容易因为措辞差异造出 AI工具 / AI开发 / AI编程 / AI编程工具 这类近义目录。

v2 用一份机器可读的注册表解决：

    <知识库根>/taxonomy.yaml

    version: 1
    categories:
      - name: AI工具与开发
        aliases: [AI工具, AI开发, AI编程]
        children:
          - name: Agent与工作流
            aliases: [Agent, AI Agent]

规则：
    1. 已注册分类优先复用
    2. 措辞不同但语义相同的说法通过 aliases 归一到同一个 name
    3. 未匹配的内容进入「待聚类」，不立刻建目录
    4. 只有相近主题累计达到 MIN_CATEGORY_SIZE 条才注册为新分类

优先使用 YAML；若环境缺少 PyYAML，则自动改用同结构的 taxonomy.json，
保证功能不因缺少第三方库而整体失效。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .layout import (
    PENDING_CLUSTER_DIR,
    TAXONOMY_FILENAME,
    TAXONOMY_JSON_FILENAME,
)

try:  # pragma: no cover - 取决于环境
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

logger = logging.getLogger("dyknow.taxonomy")

# 一个分类至少要有多少条内容才值得独立成类
MIN_CATEGORY_SIZE = 3

# 分类路径分隔符（父类/子类）
SEPARATOR = "/"

# 数字前缀，例如 "01-AI工具与开发"
_PREFIX_RE = re.compile(r"^\d+\s*[-_.、]\s*")


def normalize_key(value: str) -> str:
    """归一化用于匹配的键：去空白、去数字前缀、转小写"""
    s = str(value or "").strip()
    s = s.replace(SEPARATOR, " ").replace("\\", " ")
    s = re.sub(r"\s+", "", s)
    s = _PREFIX_RE.sub("", s)
    return s.lower()


def strip_prefix(name: str) -> str:
    """去掉目录名的数字前缀，便于模型用纯语义名称匹配"""
    return _PREFIX_RE.sub("", str(name or "").strip())


@dataclass
class Category:
    """一个分类（可含一层子分类）"""

    name: str
    aliases: list[str] = field(default_factory=list)
    children: list["Category"] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {"name": self.name}
        if self.aliases:
            d["aliases"] = list(self.aliases)
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Category":
        name = str(data.get("name", "")).strip()
        aliases = [str(a).strip() for a in (data.get("aliases") or []) if str(a).strip()]
        children = [
            cls.from_dict(c) for c in (data.get("children") or []) if isinstance(c, dict)
        ]
        return cls(name=name, aliases=aliases, children=children)


class Taxonomy:
    """分类注册表读写与解析"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.version = 1
        self.categories: list[Category] = []
        # 归一化键 → 规范分类路径
        self._index: dict[str, str] = {}

    # ── 文件 ──────────────────────────────────

    @property
    def path(self) -> Path:
        """注册表文件路径（有 PyYAML 用 yaml，否则用同结构 json）"""
        if yaml is not None:
            return self.root / TAXONOMY_FILENAME
        return self.root / TAXONOMY_JSON_FILENAME

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> "Taxonomy":
        """读取注册表。文件缺失或损坏时返回空表，不抛异常。"""
        path = self.path
        if not path.exists():
            self._rebuild_index()
            return self

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("分类注册表读取失败: %s", e)
            self._rebuild_index()
            return self

        data = None
        try:
            if path.suffix == ".json":
                data = json.loads(text)
            else:
                data = yaml.safe_load(text)
        except Exception as e:
            logger.warning("分类注册表解析失败（已按空表处理）: %s", e)

        if isinstance(data, dict):
            self.version = int(data.get("version", 1) or 1)
            raw = data.get("categories") or []
            if isinstance(raw, list):
                self.categories = [
                    Category.from_dict(c) for c in raw if isinstance(c, dict)
                ]
        self._rebuild_index()
        return self

    def save(self) -> Path:
        """写出注册表（原子写，避免同步盘锁定导致截断）"""
        from .generator import atomic_write_text

        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.version,
            "categories": [c.to_dict() for c in self.categories],
        }
        path = self.path
        if path.suffix == ".json":
            content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        else:
            content = yaml.safe_dump(
                payload, allow_unicode=True, sort_keys=False, default_flow_style=False
            )
        atomic_write_text(path, content)
        return path

    # ── 索引与解析 ────────────────────────────

    def _rebuild_index(self):
        index: dict[str, str] = {}
        for top in self.categories:
            if not top.name:
                continue
            for key in self._keys_for(top.name, top.name, top.aliases):
                index[key] = top.name
            for child in top.children:
                if not child.name:
                    continue
                path = f"{top.name}{SEPARATOR}{child.name}"
                for key in self._keys_for(child.name, path, child.aliases):
                    index[key] = path
        self._index = index

    @staticmethod
    def _keys_for(name: str, canonical: str, aliases: list[str] | None = None) -> list[str]:
        """
        一个分类可以命中哪些归一化键。

        包含：分类名、去数字前缀的分类名、全部别名，
        以及「父类去前缀 + 子类名」的扁平写法。
        """
        keys: list[str] = []
        values = [name, strip_prefix(name)]
        values.extend(str(a) for a in (aliases or []))

        parent_prefix = ""
        if SEPARATOR in canonical:
            parent, _, child = canonical.partition(SEPARATOR)
            parent_prefix = strip_prefix(parent)
            # 子类别名也可能被写成「父类+别名」
            values.extend(f"{parent_prefix}{a}" for a in (aliases or []))
            values.append(f"{parent_prefix}{child}")

        for candidate in values:
            k = normalize_key(candidate)
            if k:
                keys.append(k)
        return keys

    def resolve(self, raw: str) -> str | None:
        """
        把模型给出的分类值解析为规范分类路径。

        命中已注册分类名或别名 → 返回规范 path；否则返回 None（进入待聚类）。
        """
        if raw is None:
            return None
        s = str(raw).strip().strip('"').strip("'")
        if not s:
            return None
        return self._index.get(normalize_key(s))

    def all_paths(self) -> list[str]:
        """全部规范分类路径（含子分类）"""
        out: list[str] = []
        for top in self.categories:
            if top.name:
                out.append(top.name)
            for child in top.children:
                if child.name:
                    out.append(f"{top.name}{SEPARATOR}{child.name}")
        return out

    def top_levels(self) -> list[str]:
        return [c.name for c in self.categories if c.name]

    # ── 修改 ──────────────────────────────────

    def register(self, name_or_path: str, aliases: list[str] | None = None) -> str:
        """
        注册一个分类，返回规范路径。

        已存在（按名称或别名命中）时只补充别名，不重复创建。
        """
        raw = str(name_or_path or "").strip()
        if not raw:
            raise ValueError("分类名不能为空")

        existing = self.resolve(raw)
        if existing:
            if aliases:
                self._extend_aliases(existing, aliases)
            return existing

        aliases = [a for a in (aliases or []) if str(a).strip()]
        if SEPARATOR in raw:
            parent_name, _, child_name = raw.partition(SEPARATOR)
            parent_name = parent_name.strip()
            child_name = child_name.strip()
            parent = self._ensure_top(parent_name)
            child = self._find_child(parent, child_name)
            if child is None:
                child = Category(name=child_name)
                parent.children.append(child)
            for a in aliases:
                if a not in child.aliases:
                    child.aliases.append(a)
            canonical = f"{parent.name}{SEPARATOR}{child.name}"
        else:
            top = self._ensure_top(raw)
            for a in aliases:
                if a not in top.aliases:
                    top.aliases.append(a)
            canonical = top.name

        self._rebuild_index()
        return canonical

    def _ensure_top(self, name: str) -> Category:
        for c in self.categories:
            if c.name == name or normalize_key(c.name) == normalize_key(name):
                return c
        c = Category(name=name)
        self.categories.append(c)
        return c

    @staticmethod
    def _find_child(parent: Category, name: str) -> Category | None:
        for c in parent.children:
            if c.name == name or normalize_key(c.name) == normalize_key(name):
                return c
        return None

    def _extend_aliases(self, canonical: str, aliases: list[str]):
        if SEPARATOR in canonical:
            parent_name, _, child_name = canonical.partition(SEPARATOR)
            for top in self.categories:
                if top.name == parent_name:
                    child = self._find_child(top, child_name)
                    if child:
                        for a in aliases:
                            if a and a not in child.aliases:
                                child.aliases.append(a)
                    break
        else:
            for top in self.categories:
                if top.name == canonical:
                    for a in aliases:
                        if a and a not in top.aliases:
                            top.aliases.append(a)
                    break
        self._rebuild_index()

    def register_pending(self, names: list[str]) -> list[str]:
        """批量注册新分类（organize 在达到阈值后调用），返回成功注册的规范路径"""
        registered = []
        for n in names:
            try:
                registered.append(self.register(n))
            except ValueError:
                continue
        return registered

    # ── 目录 ──────────────────────────────────

    def target_dir(self, root: Path, category_path: str | None) -> Path:
        """
        给出某分类应落入的目录。

        category_path 为 None 时返回「待聚类」目录。
        """
        root = Path(root)
        if not category_path:
            return root / PENDING_CLUSTER_DIR
        parts = [p for p in str(category_path).split(SEPARATOR) if p]
        return root.joinpath(*parts) if parts else root / PENDING_CLUSTER_DIR


def load_taxonomy(root: Path) -> Taxonomy:
    """便捷函数：从知识库根目录加载注册表"""
    return Taxonomy(Path(root)).load()
