"""
归档与安全移动

职责（v2）：
    1. 按 AI 给出的分类建议，把笔记移动到知识库分类目录
    2. 已处理但无稳定分类 → 「待聚类」
    3. 尚未完成 AI 处理 → 「未处理」
    4. 同名文件不静默覆盖
    5. 移动后同步数据库中的 note_path

冲突处理规则（v2.1 修订）
────────────────────────
仅凭 source_id 相同**不允许**删除任何文件。只有当两个文件
source_id 相同 **且内容完全等价** 时才允许自动去重；
只要正文、转录、摘要、hash 或处理状态存在差异，两份都保留，
后者改用不冲突的文件名，并输出 warning。**不自动合并内容。**

安全约束：
    - 旧「未分类/」目录默认**不迁移**，避免粗暴搬动用户已有数据；
      需要迁移时必须显式执行 `organize --migrate`
    - 分类注册表只在某分类累计达到 MIN_CATEGORY_SIZE 条时才新建
"""

from __future__ import annotations

import logging
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import frontmatter as fm_io
from .indexer import IndexBuilder, NoteInfo
from .layout import (
    LEGACY_UNCLASSIFIED_DIR,
    PENDING_CLUSTER_DIR,
    UNPROCESSED_DIR,
    UNPROCESSED_DIR_ALIASES,
)
from .state import (
    PROCESSING_DONE,
    PROCESSING_NEEDS_REVIEW,
    PROCESSING_PENDING,
    recover_source_id,
    recover_source_id_from_filename,
)
from .taxonomy import MIN_CATEGORY_SIZE, Taxonomy

logger = logging.getLogger("dyknow.organizer")


def _normalize_note_text(text: str) -> str:
    """
    归一化笔记文本用于等价比较。

    只忽略换行风格与首尾空白；其余任何差异（正文、转录、摘要、状态字段、
    hash）都会被视为「有差异」，从而避免误删用户内容。
    """
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in t.split("\n")).strip()


@dataclass
class MovePlan:
    """一条笔记的归档计划"""

    src: Path
    dst: Path | None
    action: str          # move / skip
    reason: str
    source_id: str = ""
    category: str | None = None

    @property
    def is_move(self) -> bool:
        return self.action == "move"


@dataclass
class OrganizeResult:
    moved: list[MovePlan] = field(default_factory=list)
    skipped: list[MovePlan] = field(default_factory=list)
    failed: list[tuple[MovePlan, str]] = field(default_factory=list)
    registered: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cleaned_dirs: list[str] = field(default_factory=list)
    # 内容冲突（同 source_id 但内容不同 / 同名不同视频），两份均保留
    conflicts: list[str] = field(default_factory=list)


class Organizer:
    """按分类安全移动笔记"""

    def __init__(self, root: Path, dry_run: bool = False, migrate: bool = False,
                 source_ids: set[str] | None = None):
        self.root = Path(root)
        self.dry_run = dry_run
        self.migrate = migrate
        self.source_ids = source_ids
        self.builder = IndexBuilder(self.root)
        self.taxonomy: Taxonomy = self.builder.taxonomy
        # plan() 中判定为达到阈值的新分类：原始值 → 规范路径
        self.promoted: dict[str, str] = {}
        # apply() 过程中产生的冲突说明
        self.conflicts: list[str] = []

    # ── 计划 ──────────────────────────────────

    def plan(self) -> tuple[list[MovePlan], list[str]]:
        """生成归档计划（dry_run 时不会写入任何文件）"""
        notes = self.builder.scan()
        if self.source_ids is not None:
            notes = [n for n in notes if n.source_id in self.source_ids]

        promoted = self._promote_categories(notes)
        self.promoted = promoted

        plans: list[MovePlan] = []
        for note in notes:
            plans.append(self._plan_one(note, promoted))
        return plans, self.builder.warnings

    def _promote_categories(self, notes: list[NoteInfo]) -> dict[str, str]:
        """
        统计未注册的分类候选。

        相近主题累计达到 MIN_CATEGORY_SIZE 条时才注册为新分类，
        否则这些笔记进入「待聚类」，避免为单条内容制造近义目录。
        """
        candidates: dict[str, list[NoteInfo]] = defaultdict(list)
        for n in notes:
            if not self.migrate and self._in_legacy_dir(n.path):
                # 旧目录默认不参与统计，避免未经确认就改变既有结构
                continue
            if n.processing_status != PROCESSING_DONE:
                continue
            raw = (n.raw_category or "").strip()
            if not raw or raw.lower() == "none":
                continue
            if self.taxonomy.resolve(raw) is None:
                candidates[raw].append(n)

        promoted: dict[str, str] = {}
        for raw, items in candidates.items():
            if len(items) < MIN_CATEGORY_SIZE:
                continue
            canonical = self.taxonomy.register(raw)
            promoted[raw] = canonical
            logger.info("新分类达到阈值，已注册: %s（%d 条）", canonical, len(items))
        return promoted

    def _plan_one(self, note: NoteInfo, promoted: dict[str, str]) -> MovePlan:
        src = note.path

        # 0) 旧「未分类/」中的笔记默认不迁移，保护用户已有数据
        if not self.migrate and self._in_legacy_dir(src):
            return MovePlan(
                src, src, "skip",
                "位于旧「未分类/」，需 --migrate 才会迁移",
                note.source_id,
            )

        # 1) 从未完成 AI 处理 → 未处理/
        if note.processing_status == PROCESSING_PENDING:
            dst = self.root / UNPROCESSED_DIR / src.name
            if self._same_dir(src, dst):
                return MovePlan(src, src, "skip", "已在未处理目录", note.source_id)
            return MovePlan(src, dst, "move", "尚未完成 AI 处理", note.source_id)

        # 2) 需要复核：这类笔记**曾完成过** AI 处理，只是底层转录发生变化。
        #    它可能已经位于用户认可的分类目录，因此不得搬回「未处理/」，
        #    也不清空 category、不降级为 pending。保持当前位置即可。
        if note.processing_status == PROCESSING_NEEDS_REVIEW:
            return MovePlan(
                src, src, "skip",
                "需要重新复核摘要，保持当前位置",
                note.source_id, note.category,
            )

        # 3) 已处理：解析分类
        category = note.category
        if category is None and note.raw_category:
            category = promoted.get(note.raw_category)
            if category is None:
                category = self.taxonomy.resolve(note.raw_category)

        if category:
            dst_dir = self.taxonomy.target_dir(self.root, category)
            dst = dst_dir / src.name
            if self._same_dir(src, dst):
                return MovePlan(src, src, "skip", "已在目标分类目录", note.source_id, category)
            return MovePlan(src, dst, "move", f"归入分类 {category}", note.source_id, category)

        # 4) 已处理但没有稳定分类 → 待聚类
        dst = self.root / PENDING_CLUSTER_DIR / src.name
        if self._same_dir(src, dst):
            return MovePlan(src, src, "skip", "已在待聚类目录", note.source_id)
        reason = "分类未注册，暂待聚类" if note.raw_category else "尚无分类建议"
        return MovePlan(src, dst, "move", reason, note.source_id)

    def _in_legacy_dir(self, path: Path) -> bool:
        """是否位于旧「未分类/」目录（未显式迁移时保持原样）"""
        return path.parent.name == LEGACY_UNCLASSIFIED_DIR

    def _same_dir(self, src: Path, dst: Path) -> bool:
        try:
            return src.parent.resolve() == dst.parent.resolve()
        except OSError:
            return src.parent == dst.parent

    # ── 执行 ──────────────────────────────────

    def apply(self, plans: list[MovePlan]) -> OrganizeResult:
        result = OrganizeResult()
        result.registered = sorted(set(self.promoted.values()))

        if self.dry_run:
            for p in plans:
                (result.moved if p.is_move else result.skipped).append(p)
            result.warnings = list(self.builder.warnings)
            return result

        # 持久化注册表（仅在有分类时写出，不为空知识库创建文件）
        if self.taxonomy.categories:
            self.taxonomy.save()

        db = self._open_db()
        try:
            for p in plans:
                if not p.is_move:
                    result.skipped.append(p)
                    continue
                try:
                    real_dst, conflict_note = self._safe_move(p.src, p.dst)
                except Exception as e:  # noqa: BLE001 - 单条失败不阻断整批
                    result.failed.append((p, str(e)))
                    logger.warning("移动失败 %s: %s", p.src.name, e)
                    continue

                if conflict_note:
                    result.conflicts.append(conflict_note)
                    logger.warning(conflict_note)

                self._sync_note(p, real_dst)
                if db is not None and p.source_id:
                    try:
                        db.update_note_path(p.source_id, str(real_dst))
                    except Exception as e:  # noqa: BLE001
                        logger.warning("同步数据库路径失败 %s: %s", p.source_id, e)
                result.moved.append(p)
        finally:
            if db is not None:
                db.close()

        if self.migrate:
            result.cleaned_dirs = self._cleanup_legacy_dirs()

        result.warnings = list(self.builder.warnings)
        return result

    def _safe_move(self, src: Path, dst: Path | None) -> tuple[Path, str]:
        """
        移动文件，绝不静默覆盖。返回 (实际目标路径, 冲突说明)。

        冲突说明为空串表示没有发生冲突。

        规则：

            目标不存在
                → 直接移动

            source_id 相同 **且内容完全等价**
                → 安全去重：保留目标，移除源（两份本来完全一样）

            source_id 相同 **但内容有差异**（正文 / 转录 / 摘要 / hash / 状态）
                → **两份都保留**，后者改用不冲突文件名，并输出 warning

            source_id 不同（不同视频但同名）
                → 用不冲突文件名，两份都保留

            任一方没有可靠 source_id（v1 旧笔记等）
                → 保守处理：视为不同文件，两份都保留
        """
        if dst is None:
            raise ValueError("目标路径为空")

        dst.parent.mkdir(parents=True, exist_ok=True)
        conflict_note = ""

        if dst.exists() and dst.resolve() != src.resolve():
            same_video = self._same_video(src, dst)
            identical = self._same_content(src, dst)

            if same_video and identical:
                # 内容完全一致 → 安全去重
                src.unlink(missing_ok=True)
                return dst, ""

            if same_video:
                conflict_note = (
                    f"同一 source_id 但内容不同，两份均保留（未自动合并）: "
                    f"{src.name} 与 {dst.name}"
                )
            else:
                conflict_note = (
                    f"目标已存在同名文件，改用不冲突文件名: {dst.name}"
                )
            dst = self._unique_path(dst)

        if not src.exists():
            raise FileNotFoundError(f"源文件不存在: {src}")

        shutil.move(str(src), str(dst))
        return dst, conflict_note

    def _same_video(self, a: Path, b: Path) -> bool:
        """
        按 source_id 判断两个文件是否为同一视频。

        任一方无法恢复出可靠 ID 时返回 False（保守：不做去重）。
        """
        ida = self._read_source_id(a)
        idb = self._read_source_id(b)
        if not ida or not idb:
            return False
        return ida == idb

    @staticmethod
    def _same_content(a: Path, b: Path) -> bool:
        """两份笔记内容是否完全等价（只忽略换行风格与首尾空白）"""
        ta = fm_io.read_text(a)
        tb = fm_io.read_text(b)
        if not ta or not tb:
            return False
        return _normalize_note_text(ta) == _normalize_note_text(tb)

    @staticmethod
    def _read_source_id(path: Path) -> str:
        """读取笔记的稳定唯一 ID（兼容 v1 的 video_id / source / 文件名）"""
        meta, _ = fm_io.read_note(path)
        return recover_source_id(meta) or recover_source_id_from_filename(path)

    @staticmethod
    def _unique_path(dst: Path) -> Path:
        """为目标文件名寻找不冲突的可用路径（不覆盖任何已有文件）"""
        stem, suffix = dst.stem, dst.suffix
        parent = dst.parent
        for i in range(2, 1000):
            candidate = parent / f"{stem}-{i}{suffix}"
            if not candidate.exists():
                return candidate
        raise FileExistsError(f"无法为 {dst.name} 找到不冲突的文件名")

    def _sync_note(self, plan: MovePlan, real_dst: Path):
        """
        移动完成后把分类字段规范化为注册表中的正式名称。

        使用逐行替换（frontmatter.set_field），不重写整个 frontmatter，
        因此笔记里的多行列表、嵌套字段与自定义字段都不会丢失。
        """
        if not plan.category:
            return
        try:
            meta, _ = fm_io.read_note(real_dst)
            if str(meta.get("category") or "").strip() == plan.category:
                return
            fm_io.update_meta(real_dst, {"category": plan.category})
        except Exception as e:  # noqa: BLE001
            logger.warning("写入分类字段失败 %s: %s", real_dst.name, e)

    def _cleanup_legacy_dirs(self) -> list[str]:
        """迁移模式下清理空的旧目录（只删空目录）"""
        cleaned: list[str] = []
        for name in UNPROCESSED_DIR_ALIASES + (PENDING_CLUSTER_DIR,):
            d = self.root / name
            if not d.is_dir():
                continue
            if any(d.iterdir()):
                continue
            try:
                d.rmdir()
                cleaned.append(name)
            except OSError:
                pass
        return cleaned

    def _open_db(self):
        """尽力打开数据库；不可用时不影响文件归档"""
        try:
            from .db import get_db

            return get_db()
        except Exception as e:  # noqa: BLE001
            logger.warning("数据库不可用，跳过路径同步: %s", e)
            return None


def organize(root: Path, dry_run: bool = False, migrate: bool = False) -> OrganizeResult:
    """便捷入口：生成计划并执行归档"""
    org = Organizer(root, dry_run=dry_run, migrate=migrate)
    plans, _ = org.plan()
    return org.apply(plans)
