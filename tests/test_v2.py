"""
DyKnow v2 测试套件

覆盖任务书要求的六类行为：
    A. 旧 status 映射
    B. transcript hash 幂等
    C. 索引（排除自身 / 统计 / 坏笔记容错 / 移动后链接正确）
    D. 分类与 taxonomy（alias 匹配 / 不随意造目录 / 阈值建类）
    E. 文件冲突（不同 source_id 同标题不互相覆盖）
    F. 兼容性（旧笔记可读、核心路径未失效）

运行（在项目根目录执行，解释器按平台选择）:

    Windows:      .venv/Scripts/python.exe tests/test_v2.py
    macOS/Linux:  .venv/bin/python        tests/test_v2.py

或统一使用一键入口:

    python tests/run_all.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def make_note(
    path: Path,
    source_id: str,
    title: str,
    category: str = "",
    processed: bool = False,
    transcript: str = "这是转录正文内容，用于测试。",
    summary: str = "",
    processing_status: str | None = None,
) -> Path:
    """构造一条笔记文件（用于测试，不依赖网络）"""
    from dyknow import frontmatter as fm
    from dyknow import state

    p_status = processing_status or ("done" if processed else "pending")
    t_hash = state.transcript_hash(transcript)
    meta = {
        "title": title,
        "video_id": source_id,
        "source_id": source_id,
        "source": f"https://www.douyin.com/video/{source_id}",
        "author": "测试作者",
        "platform": "douyin",
        "date": "2026-01-01",
        "likes": 10,
        "transcript_status": "done" if transcript else "pending",
        "processing_status": p_status,
        "category": category,
        "transcript_hash": t_hash,
        "summarized_from_hash": t_hash if p_status in ("done", "needs_review") else "",
        "status": {
            "done": "done",
            "needs_review": "done",
        }.get(p_status, "transcribed" if transcript else "indexed"),
    }
    body = (
        f"\n# {title}\n\n"
        "## 🤖 AI 摘要\n\n"
        f"### 一句话总结\n{summary or '_待模型填写_'}\n\n"
        "### 要点\n- 要点甲：具体方法\n- 要点乙：具体数据\n\n"
        '### 金句\n- "金句原文内容示例"（转录原文）\n\n'
        "---\n\n"
        "## 📝 完整转写\n\n"
        f"{transcript}\n"
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fm.dump_frontmatter(meta, body), encoding="utf-8")
    return path


class BaseCase(unittest.TestCase):
    """隔离运行环境：临时知识库 + 临时数据库，避免污染项目 data/"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dyknow_v2_"))
        self.root = self.tmp / "kb"
        self.root.mkdir(parents=True, exist_ok=True)

        from dyknow import config as cfg_mod
        from dyknow import db as db_mod

        self._saved = (cfg_mod.config.data_dir, cfg_mod.config.output_dir)
        cfg_mod.config.data_dir = self.tmp / "_data"
        cfg_mod.config.output_dir = self.root
        db_mod._db_instance = None

    def tearDown(self):
        from dyknow import config as cfg_mod
        from dyknow import db as db_mod

        if db_mod._db_instance is not None:
            db_mod._db_instance.close()
        db_mod._db_instance = None
        cfg_mod.config.data_dir, cfg_mod.config.output_dir = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
# A. 旧 status 映射
# ═══════════════════════════════════════════════════════════════


class TestLegacyStatusMapping(BaseCase):

    def test_indexed(self):
        from dyknow.state import PROCESSING_PENDING, TRANSCRIPT_PENDING, resolve_state
        self.assertEqual(resolve_state({"status": "indexed"}),
                         (TRANSCRIPT_PENDING, PROCESSING_PENDING))

    def test_transcribed(self):
        from dyknow.state import PROCESSING_PENDING, TRANSCRIPT_DONE, resolve_state
        self.assertEqual(resolve_state({"status": "transcribed"}),
                         (TRANSCRIPT_DONE, PROCESSING_PENDING))

    def test_done(self):
        from dyknow.state import PROCESSING_DONE, TRANSCRIPT_DONE, resolve_state
        self.assertEqual(resolve_state({"status": "done"}),
                         (TRANSCRIPT_DONE, PROCESSING_DONE))

    def test_failed_is_not_pending(self):
        from dyknow.state import TRANSCRIPT_FAILED, resolve_state
        t, _p = resolve_state({"status": "failed"})
        self.assertEqual(t, TRANSCRIPT_FAILED)

    def test_pipeline_statuses(self):
        from dyknow.state import TRANSCRIPT_PENDING, resolve_state
        for s in ("pending_index", "pending_download", "pending_audio", "pending_transcribe"):
            self.assertEqual(resolve_state({"status": s})[0], TRANSCRIPT_PENDING, s)

    def test_new_fields_take_precedence(self):
        from dyknow.state import PROCESSING_DONE, TRANSCRIPT_DONE, resolve_state
        meta = {
            "status": "indexed",
            "transcript_status": "done",
            "processing_status": "done",
        }
        self.assertEqual(resolve_state(meta), (TRANSCRIPT_DONE, PROCESSING_DONE))

    def test_infer_from_body_when_no_status(self):
        from dyknow.state import PROCESSING_PENDING, TRANSCRIPT_DONE, resolve_state
        body = "## 📝 完整转写\n\n这里是真实的转录内容。\n"
        self.assertEqual(resolve_state({}, body), (TRANSCRIPT_DONE, PROCESSING_PENDING))
        body_placeholder = "## 📝 完整转写\n\n_待转录..._\n"
        self.assertEqual(resolve_state({}, body_placeholder)[0], "pending")

    def test_legacy_status_is_still_written(self):
        from dyknow.state import legacy_status_of
        self.assertEqual(legacy_status_of("pending", "pending"), "indexed")
        self.assertEqual(legacy_status_of("done", "pending"), "transcribed")
        self.assertEqual(legacy_status_of("done", "done"), "done")
        self.assertEqual(legacy_status_of("failed", "pending"), "failed")


# ═══════════════════════════════════════════════════════════════
# B. transcript hash
# ═══════════════════════════════════════════════════════════════


class TestTranscriptHash(BaseCase):

    def test_same_text_same_hash(self):
        from dyknow.state import transcript_hash
        self.assertEqual(transcript_hash("你好世界"), transcript_hash("你好世界"))

    def test_line_endings_do_not_matter(self):
        from dyknow.state import transcript_hash
        self.assertEqual(transcript_hash("甲\r\n乙"), transcript_hash("甲\n乙"))

    def test_trailing_whitespace_does_not_matter(self):
        from dyknow.state import transcript_hash
        self.assertEqual(transcript_hash("甲 乙\n\n"), transcript_hash("甲 乙"))

    def test_content_change_changes_hash(self):
        from dyknow.state import transcript_hash
        self.assertNotEqual(transcript_hash("原内容"), transcript_hash("改过的内容"))

    def test_empty_returns_empty(self):
        from dyknow.state import transcript_hash
        self.assertEqual(transcript_hash(""), "")
        self.assertEqual(transcript_hash("   \n  "), "")

    def test_is_summary_up_to_date(self):
        from dyknow.state import (
            PROCESSING_DONE,
            is_summary_up_to_date,
            transcript_hash,
        )
        text = "一段转录内容"
        h = transcript_hash(text)
        self.assertTrue(is_summary_up_to_date({
            "transcript_hash": h,
            "summarized_from_hash": h,
            "processing_status": PROCESSING_DONE,
        }))
        # 转录变了 → 需要重新摘要
        self.assertFalse(is_summary_up_to_date({
            "transcript_hash": transcript_hash("另一段内容"),
            "summarized_from_hash": h,
            "processing_status": PROCESSING_DONE,
        }))

    def test_needs_resummarize(self):
        from dyknow.state import needs_resummarize, transcript_hash
        text = "转录内容"
        self.assertTrue(needs_resummarize({}, text))
        self.assertFalse(needs_resummarize(
            {"summarized_from_hash": transcript_hash(text)}, text))

    def test_update_transcript_writes_hash(self):
        """回填转录后笔记应带上可用的 hash"""
        from dyknow import frontmatter as fm
        from dyknow.generator import generate_note, update_transcript
        from dyknow.state import transcript_hash

        p = generate_note(title="哈希测试", aweme_id="9999999999999999999",
                          output_dir=self.root / "未处理")
        update_transcript(p, "回填的转录正文")
        meta, _ = fm.read_note(p)
        self.assertEqual(meta["transcript_status"], "done")
        self.assertEqual(meta["transcript_hash"], transcript_hash("回填的转录正文"))
        self.assertEqual(meta["status"], "transcribed")


# ═══════════════════════════════════════════════════════════════
# C. 索引
# ═══════════════════════════════════════════════════════════════


class TestIndex(BaseCase):

    def _three_processed(self, category="AI工具"):
        for i in range(3):
            make_note(self.root / "未处理" / f"视频{i}_{i}000000000000000000.md",
                      f"{i}000000000000000000", f"视频{i}",
                      category=category, processed=True,
                      summary=f"第{i}条视频的具体结论")

    def test_index_excludes_itself(self):
        from dyknow.indexer import rebuild_index
        from dyknow.layout import INDEX_FILENAME

        make_note(self.root / "未处理" / "a_1111111111111111111.md",
                  "1111111111111111111", "标题A")
        first = rebuild_index(self.root)
        self.assertEqual(first["stats"]["total"], 1)

        # 再跑一次，索引文件本身不得被计入
        second = rebuild_index(self.root)
        self.assertEqual(second["stats"]["total"], 1)
        self.assertTrue((self.root / INDEX_FILENAME).exists())

    def test_stats_counting(self):
        from dyknow.indexer import rebuild_index
        from dyknow.taxonomy import Taxonomy

        # 先注册分类：已注册的 category 才能解析到正式分类，
        # 未注册的分类会被视为「暂无稳定分类」计入待聚类。
        tax = Taxonomy(self.root)
        tax.register("AI工具")
        tax.save()

        self._three_processed()
        # 一条已处理但无分类
        make_note(self.root / "未处理" / "无分类_9999999999999999999.md",
                  "9999999999999999999", "无分类视频", processed=True)
        # 一条尚未处理
        make_note(self.root / "未处理" / "待处理_8888888888888888888.md",
                  "8888888888888888888", "待处理视频", processed=False)

        result = rebuild_index(self.root)
        st = result["stats"]
        self.assertEqual(st["total"], 5)
        self.assertEqual(st["processed"], 4)
        self.assertEqual(st["pending"], 1)
        self.assertEqual(st["pending_cluster"], 1)

    def test_bad_note_does_not_crash(self):
        from dyknow.indexer import rebuild_index

        make_note(self.root / "未处理" / "ok_1111111111111111111.md",
                  "1111111111111111111", "正常笔记")
        (self.root / "普通笔记.md").write_text("没有 frontmatter 的普通笔记", encoding="utf-8")
        (self.root / "半截.md").write_text("---\ntitle: 没闭合\n", encoding="utf-8")

        result = rebuild_index(self.root)
        self.assertEqual(result["stats"]["total"], 1)
        self.assertTrue(len(result["warnings"]) >= 1)

    def test_link_after_move(self):
        from dyknow.indexer import rebuild_index
        from dyknow.layout import INDEX_FILENAME, UNPROCESSED_DIR
        from dyknow.organizer import organize

        self._three_processed()
        rebuild_index(self.root)
        before = (self.root / INDEX_FILENAME).read_text(encoding="utf-8")
        self.assertIn(f"[[{UNPROCESSED_DIR}/", before)

        organize(self.root)
        rebuild_index(self.root)
        after = (self.root / INDEX_FILENAME).read_text(encoding="utf-8")
        self.assertIn("[[AI工具/", after)
        self.assertNotIn(f"[[{UNPROCESSED_DIR}/", after)

    def test_root_index_keeps_only_oneline(self):
        """根索引只放一句话总结，不塞完整要点/金句"""
        from dyknow.indexer import rebuild_index
        from dyknow.layout import INDEX_FILENAME

        self._three_processed()
        organize_ok = self.root  # 保持可读性
        from dyknow.organizer import organize
        organize(organize_ok)
        rebuild_index(self.root)

        text = (self.root / INDEX_FILENAME).read_text(encoding="utf-8")
        self.assertIn("第0条视频的具体结论", text)
        # 要点不会被写进根索引
        self.assertNotIn("要点甲", text)


# ═══════════════════════════════════════════════════════════════
# D. taxonomy / 分类
# ═══════════════════════════════════════════════════════════════


class TestTaxonomy(BaseCase):

    def test_save_and_load_roundtrip(self):
        from dyknow.taxonomy import Taxonomy

        t = Taxonomy(self.root)
        t.register("AI工具与开发", ["AI工具", "AI开发"])
        t.register("AI工具与开发/Agent与工作流", ["Agent"])
        path = t.save()
        self.assertTrue(path.exists())

        again = Taxonomy(self.root).load()
        self.assertEqual(len(again.categories), 1)
        self.assertEqual(len(again.categories[0].children), 1)

    def test_alias_resolves_to_canonical(self):
        from dyknow.taxonomy import Taxonomy

        t = Taxonomy(self.root)
        t.register("01-AI工具与开发", ["AI工具", "AI编程"])
        for probe in ("AI工具", "AI编程", "AI工具与开发", "01-AI工具与开发", "  ai  编程  "):
            self.assertEqual(t.resolve(probe), "01-AI工具与开发", probe)

    def test_unknown_category_returns_none(self):
        from dyknow.taxonomy import Taxonomy

        t = Taxonomy(self.root)
        t.register("AI工具")
        self.assertIsNone(t.resolve("完全没听过的分类"))

    def test_corrupt_taxonomy_does_not_crash(self):
        from dyknow.layout import TAXONOMY_FILENAME
        from dyknow.taxonomy import Taxonomy

        (self.root / TAXONOMY_FILENAME).write_text("::: 这不是 yaml :::", encoding="utf-8")
        tax = Taxonomy(self.root).load()
        self.assertEqual(tax.categories, [])

    def test_single_item_does_not_create_category(self):
        """未匹配内容不会自动产生大量新目录"""
        from dyknow.organizer import organize
        from dyknow.layout import PENDING_CLUSTER_DIR

        for i in range(5):
            make_note(self.root / "未处理" / f"n{i}.md", f"700000000000000000{i}",
                      f"视频{i}", category=f"独特分类{i}", processed=True)

        res = organize(self.root)
        self.assertEqual(res.registered, [])
        self.assertFalse((self.root / "taxonomy.yaml").exists())

        clustered = list((self.root / PENDING_CLUSTER_DIR).glob("*.md"))
        self.assertEqual(len(clustered), 5)

    def test_threshold_registers_new_category(self):
        from dyknow.organizer import organize
        from dyknow.taxonomy import MIN_CATEGORY_SIZE, Taxonomy

        for i in range(MIN_CATEGORY_SIZE):
            make_note(self.root / "未处理" / f"t{i}.md", f"710000000000000000{i}",
                      f"阈值视频{i}", category="量子计算", processed=True)

        res = organize(self.root)
        self.assertEqual(res.registered, ["量子计算"])

        tax = Taxonomy(self.root).load()
        self.assertIn("量子计算", tax.all_paths())

    def test_alias_prevents_synonym_dirs(self):
        """措辞不同但语义相同应归入同一分类，而不是各建一个目录"""
        from dyknow.organizer import organize
        from dyknow.taxonomy import Taxonomy

        tax = Taxonomy(self.root)
        tax.register("AI工具与开发", ["AI工具", "AI编程", "AI开发"])
        tax.save()

        for i, cat in enumerate(["AI工具", "AI编程", "AI开发", "AI工具"]):
            make_note(self.root / "未处理" / f"s{i}.md", f"720000000000000000{i}",
                      f"同义视频{i}", category=cat, processed=True)

        organize(self.root)
        dirs = {p.name for p in self.root.iterdir() if p.is_dir()}
        self.assertIn("AI工具与开发", dirs)
        for synonym in ("AI工具", "AI编程", "AI开发"):
            self.assertNotIn(synonym, dirs, f"{synonym} 不应单独成目录")


# ═══════════════════════════════════════════════════════════════
# E. 文件冲突
# ═══════════════════════════════════════════════════════════════


class TestFileConflict(BaseCase):

    def test_same_title_different_source_id_not_overwritten(self):
        from dyknow import frontmatter as fm
        from dyknow.organizer import organize

        # 两条不同 source_id、同文件名、同分类
        make_note(self.root / "未处理" / "同名_1111111111111111111.md",
                  "1111111111111111111", "同名视频", category="AI工具", processed=True)
        make_note(self.root / "未处理" / "子目录" / "同名_2222222222222222222.md",
                  "2222222222222222222", "同名视频", category="AI工具", processed=True)
        for i in range(3, 6):
            make_note(self.root / "未处理" / f"x{i}.md", f"730000000000000000{i}",
                      f"补位{i}", category="AI工具", processed=True)

        organize(self.root)
        target = self.root / "AI工具"
        files = list(target.glob("同名*.md"))
        self.assertEqual(len(files), 2, f"两份同名文件都应保留: {files}")

        ids = {str(fm.read_note(f)[0].get("source_id")) for f in files}
        self.assertEqual(ids, {"1111111111111111111", "2222222222222222222"})

    def test_same_source_id_different_summary_kept(self):
        """同 source_id 但摘要不同 → 两份都保留，不自动去重、不自动合并"""
        from dyknow.organizer import organize

        make_note(self.root / "未处理" / "S_1111111111111111111.md",
                  "1111111111111111111", "同源视频",
                  category="AI工具", processed=True, summary="第一种摘要")
        make_note(self.root / "未处理" / "更深" / "S_1111111111111111111.md",
                  "1111111111111111111", "同源视频",
                  category="AI工具", processed=True, summary="完全不同的第二种摘要")
        for i in range(3, 6):
            make_note(self.root / "未处理" / f"z{i}.md", f"780000000000000000{i}",
                      f"补位{i}", category="AI工具", processed=True)

        res = organize(self.root)
        found = sorted(p.name for p in (self.root / "AI工具").glob("S_*.md"))
        self.assertEqual(len(found), 2, f"摘要不同的两份都应保留: {found}")
        self.assertTrue(res.conflicts, "应输出冲突说明")

    def test_same_source_id_different_transcript_kept(self):
        """同 source_id 但转录不同 → 两份都保留"""
        from dyknow.organizer import organize

        make_note(self.root / "未处理" / "T_1111111111111111111.md",
                  "1111111111111111111", "同源视频",
                  category="AI工具", processed=True, transcript="第一版转录内容")
        make_note(self.root / "未处理" / "更深" / "T_1111111111111111111.md",
                  "1111111111111111111", "同源视频",
                  category="AI工具", processed=True,
                  transcript="第二版转录内容，明显更长也更完整")
        for i in range(3, 6):
            make_note(self.root / "未处理" / f"w{i}.md", f"790000000000000000{i}",
                      f"补位{i}", category="AI工具", processed=True)

        organize(self.root)
        found = list((self.root / "AI工具").glob("T_*.md"))
        self.assertEqual(len(found), 2, f"转录不同的两份都应保留: {found}")

    def test_same_source_id_deduplicated(self):
        from dyknow.organizer import organize

        for path in (
            self.root / "未处理" / "D_1111111111111111111.md",
            self.root / "未处理" / "更深" / "D_1111111111111111111.md",
        ):
            make_note(path, "1111111111111111111", "重复视频",
                      category="AI工具", processed=True)
        for i in range(3, 6):
            make_note(self.root / "未处理" / f"y{i}.md", f"740000000000000000{i}",
                      f"补位{i}", category="AI工具", processed=True)

        organize(self.root)
        found = list((self.root / "AI工具").glob("D_1111111111111111111*.md"))
        self.assertEqual(len(found), 1, f"同一 source_id 只应保留一份: {found}")

    def test_legacy_dir_not_migrated_by_default(self):
        from dyknow.organizer import organize
        from dyknow.layout import LEGACY_UNCLASSIFIED_DIR

        legacy = self.root / LEGACY_UNCLASSIFIED_DIR
        make_note(legacy / "旧_1111111111111111111.md", "1111111111111111111",
                  "旧笔记", category="AI工具", processed=True)
        for i in range(3):
            make_note(legacy / f"旧{i}.md", f"75{i}0000000000000000{i}",
                      f"旧视频{i}", category="AI工具", processed=True)

        organize(self.root)
        self.assertTrue(legacy.exists())
        self.assertEqual(len(list(legacy.glob("*.md"))), 4)
        # 未迁移 → 不因旧目录内容触发建类
        self.assertFalse((self.root / "AI工具").exists())

    def test_legacy_dir_migrated_with_flag(self):
        from dyknow.organizer import organize
        from dyknow.layout import LEGACY_UNCLASSIFIED_DIR

        legacy = self.root / LEGACY_UNCLASSIFIED_DIR
        for i in range(3):
            make_note(legacy / f"旧{i}.md", f"76{i}0000000000000000{i}",
                      f"旧视频{i}", category="AI工具", processed=True)

        organize(self.root, migrate=True)
        self.assertTrue((self.root / "AI工具").exists())
        self.assertEqual(len(list((self.root / "AI工具").glob("*.md"))), 3)


# ═══════════════════════════════════════════════════════════════
# F. 兼容性
# ═══════════════════════════════════════════════════════════════


class TestCompatibility(BaseCase):

    LEGACY_NOTE = """---
title: "旧笔记"
video_id: "1111111111111111111"
author: "老王"
likes: 100
status: done
tags: [抖音, 收藏]
---

# 旧笔记

## 📌 概述

> 旧笔记的一句话总结

## 📝 完整转写

旧笔记的转录正文。

## 🎬 章节要点

- 旧要点

## 💡 金句 / 可复用素材

- "旧金句原文内容"
"""

    def _write_legacy(self) -> Path:
        p = self.root / "未分类" / "旧笔记_1111111111111111111.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.LEGACY_NOTE, encoding="utf-8")
        return p

    def test_legacy_note_readable(self):
        from dyknow.indexer import rebuild_index

        self._write_legacy()
        result = rebuild_index(self.root)
        self.assertEqual(result["stats"]["total"], 1)
        self.assertEqual(result["stats"]["processed"], 1)

        text = (self.root / "知识库索引.md").read_text(encoding="utf-8")
        self.assertIn("旧笔记的一句话总结", text)

    def test_legacy_note_survives_transcript_refill(self):
        from dyknow import frontmatter as fm
        from dyknow.generator import update_transcript

        p = self._write_legacy()
        update_transcript(p, "新的转录正文")
        meta, body = fm.read_note(p)
        self.assertEqual(meta["transcript_status"], "done")
        self.assertIn("新的转录正文", body)
        # 旧字段仍在
        self.assertIn("status", meta)

    def test_parse_module_still_works(self):
        from dyknow.parser import extract_url, extract_video_id

        text = "7.64 https://v.douyin.com/abc123/ 复制打开抖音"
        self.assertIn("v.douyin.com", extract_url(text))
        self.assertEqual(
            extract_video_id("https://www.douyin.com/video/7234567890123456789"),
            "7234567890123456789",
        )

    def test_syncer_entrypoints_exist(self):
        from dyknow.syncer import Syncer

        for name in ("sync", "browse", "_phase2_transcribe", "_index_items"):
            self.assertTrue(hasattr(Syncer, name), name)

    def test_scraper_search_exists(self):
        from dyknow import scraper

        self.assertTrue(callable(getattr(scraper, "search", None)))

    def test_cli_new_commands_registered(self):
        r = subprocess.run(
            [sys.executable, "-m", "dyknow", "--help"],
            cwd=str(PROJECT_ROOT), capture_output=True, timeout=30,
        )
        out = (r.stdout or b"").decode("utf-8", "replace")
        for cmd in ("index", "organize", "taxonomy", "browse", "parse", "sync"):
            self.assertIn(cmd, out, f"CLI 缺少命令: {cmd}")

    def test_browse_transcribe_enters_pipeline(self):
        """
        browse --transcribe 必须进入已有下载/转录流水线（入口验证）。

        注意：这是入口逻辑的 mock 测试，不能替代 browse 的真实接口验证。
        """
        from unittest import mock

        from dyknow.scraper import FavoriteItem
        from dyknow.syncer import Syncer

        syncer = Syncer()
        item = FavoriteItem(
            aweme_id="8888888888888888888",
            title="入口测试",
            author="某人",
            video_url="http://example.com/v.mp4",
        )
        calls = {"n": 0}

        def fake_phase2(*_a, **_kw):
            calls["n"] += 1
            return 0

        with mock.patch(
            "dyknow.syncer.scrape_search",
            new=mock.AsyncMock(return_value=[item]),
        ), mock.patch.object(syncer, "_ensure_login", return_value=True), \
                mock.patch.object(syncer, "_index_items", return_value=1), \
                mock.patch.object(syncer, "_phase2_transcribe", side_effect=fake_phase2):
            rc = syncer.browse(keyword="测试", count=1, transcribe=True)

        self.assertEqual(rc, 0)
        self.assertEqual(calls["n"], 1, "transcribe=True 必须进入转录流水线")

    def test_browse_without_transcribe_skips_pipeline(self):
        from unittest import mock

        from dyknow.scraper import FavoriteItem
        from dyknow.syncer import Syncer

        syncer = Syncer()
        item = FavoriteItem(
            aweme_id="8888888888888888887",
            title="入口测试2",
            video_url="http://example.com/y.mp4",
        )

        with mock.patch(
            "dyknow.syncer.scrape_search",
            new=mock.AsyncMock(return_value=[item]),
        ), mock.patch.object(syncer, "_ensure_login", return_value=True), \
                mock.patch.object(syncer, "_index_items", return_value=1), \
                mock.patch.object(syncer, "_phase2_transcribe") as fake:
            rc = syncer.browse(keyword="测试", count=1, transcribe=False)

        self.assertEqual(rc, 0)
        fake.assert_not_called()

    def test_cli_index_and_organize_runnable(self):
        from dyknow.indexer import rebuild_index

        make_note(self.root / "未处理" / "c_1111111111111111111.md",
                  "1111111111111111111", "命令测试")
        rebuild_index(self.root)

        r = subprocess.run(
            [sys.executable, "-m", "dyknow", "index", "--output", str(self.root)],
            cwd=str(PROJECT_ROOT), capture_output=True, timeout=60,
        )
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))

        r = subprocess.run(
            [sys.executable, "-m", "dyknow", "organize",
             "--output", str(self.root), "--dry-run"],
            cwd=str(PROJECT_ROOT), capture_output=True, timeout=60,
        )
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))


# ═══════════════════════════════════════════════════════════════
# G. frontmatter 数据保全（v2.1 新增）
# ═══════════════════════════════════════════════════════════════


class TestFrontmatterPreservation(BaseCase):
    """更新 DyKnow 字段时，多行列表 / 嵌套字段 / 未知字段 / 中文 / URL 都不得丢失"""

    RAW = """---
title: "中文标题：测试"
video_id: "7650813418760359203"
source: https://www.douyin.com/video/7650813418760359203
author: "小天fotos"
duration: 09:03
tags:
  - 多Agent
  - 编排
  - 上下文工程
topics: [AI Agent, 软件工程]
custom_field: "用户自己加的字段"
nested:
  a: 1
  b: 2
status: done
---

# 中文标题

正文内容
"""

    def setUp(self):
        super().setUp()
        self.note = self.root / "note.md"
        self.note.write_text(self.RAW, encoding="utf-8")

    def test_multiline_list_readable(self):
        from dyknow import frontmatter as fm

        meta, _ = fm.read_note(self.note)
        self.assertEqual(meta.get("tags"), ["多Agent", "编排", "上下文工程"])
        self.assertEqual(meta.get("topics"), ["AI Agent", "软件工程"])
        self.assertEqual(meta.get("nested"), {"a": 1, "b": 2})

    def test_duration_not_mangled(self):
        """YAML 1.1 的 60 进制陷阱：09:03 不得被解析成整数 543"""
        from dyknow import frontmatter as fm

        meta, _ = fm.read_note(self.note)
        self.assertEqual(str(meta.get("duration")), "09:03")

    def test_update_keeps_all_other_fields(self):
        from dyknow import frontmatter as fm

        ok = fm.update_meta(self.note, {
            "status": "transcribed",
            "processing_status": "pending",
            "category": "AI工具",
            "transcript_hash": "abc123",
        })
        self.assertTrue(ok)

        meta, body = fm.read_note(self.note)
        # 复杂 / 未知字段必须仍然存在，语义不变
        self.assertEqual(meta.get("tags"), ["多Agent", "编排", "上下文工程"])
        self.assertEqual(meta.get("topics"), ["AI Agent", "软件工程"])
        self.assertEqual(meta.get("nested"), {"a": 1, "b": 2})
        self.assertEqual(meta.get("custom_field"), "用户自己加的字段")
        # 中文与 URL 语义不变
        self.assertEqual(meta.get("title"), "中文标题：测试")
        self.assertEqual(
            meta.get("source"),
            "https://www.douyin.com/video/7650813418760359203",
        )
        self.assertEqual(str(meta.get("video_id")), "7650813418760359203")
        # 目标字段已更新
        self.assertEqual(meta.get("status"), "transcribed")
        self.assertEqual(meta.get("processing_status"), "pending")
        self.assertEqual(meta.get("category"), "AI工具")
        self.assertEqual(meta.get("transcript_hash"), "abc123")
        # 正文未受影响
        self.assertIn("正文内容", body)

    def test_complex_unknown_line_survives(self):
        """内置解析器读不懂的原始行也不得被删除"""
        from dyknow import frontmatter as fm

        raw = (
            "---\ntitle: t\n"
            "weird_field: {a: 1, b: [2, 3]}\n"
            "status: indexed\n"
            "---\n\nbody\n"
        )
        self.note.write_text(raw, encoding="utf-8")
        fm.update_meta(self.note, {"status": "done"})
        text = self.note.read_text(encoding="utf-8")
        self.assertIn("weird_field: {a: 1, b: [2, 3]}", text)
        self.assertIn("status: done", text)

    def test_generator_update_transcript_keeps_fields(self):
        """走真实调用链：回填转录也不能吃掉旧笔记的复杂字段"""
        from dyknow import frontmatter as fm
        from dyknow.generator import update_transcript

        update_transcript(self.note, "新的转录正文")
        meta, body = fm.read_note(self.note)
        self.assertEqual(meta.get("tags"), ["多Agent", "编排", "上下文工程"])
        self.assertEqual(meta.get("custom_field"), "用户自己加的字段")
        self.assertEqual(meta.get("nested"), {"a": 1, "b": 2})
        self.assertEqual(meta.get("transcript_status"), "done")
        self.assertIn("新的转录正文", body)

    def test_set_field_replaces_block_list_cleanly(self):
        """把多行列表改成单行后，不得残留孤立的 `- item` 行"""
        from dyknow import frontmatter as fm

        fm.update_meta(self.note, {"tags": ["新标签"]})
        meta, _ = fm.read_note(self.note)
        self.assertEqual(meta.get("tags"), ["新标签"])
        text = self.note.read_text(encoding="utf-8")
        self.assertNotIn("- 多Agent", text)
        self.assertNotIn("- 编排", text)


# ═══════════════════════════════════════════════════════════════
# H. 真正的 v1 旧笔记兼容（v2.1 新增）
# ═══════════════════════════════════════════════════════════════


class TestLegacyV1Note(BaseCase):
    """
    严格按 v1 generator 的真实输出结构构造 fixture：
    没有 source_id、只有旧 status、旧版正文结构、无 §AI 摘要 区块。
    """

    V1_DONE = """---
title: "v1旧笔记：XGBoost调参"
video_id: "7650813418760359203"
source: "https://www.douyin.com/video/7650813418760359203"
author: "老王"
author_id: "MS4wLjABAAAAxxxx"
platform: douyin
date: 2026-06-22
duration: 09:03
likes: 1234
comments: 56
shares: 78
plays: 0
evidence_level: transcript
status: done
tags: [抖音, 收藏]
---

# v1旧笔记：XGBoost调参

## 📌 概述

> 用分仓法把回撤控制在 10% 以内

## 📊 数据快照

| 指标 | 数值 |
|------|------|
| 作者 | 老王 |
| 时长 | 09:03 |

> [!info] 视频信息
> - **作者**: [老王](https://www.douyin.com/user/MS4wLjABAAAAxxxx)
> - **链接**: [抖音原视频](https://www.douyin.com/video/7650813418760359203)
> - **日期**: 2026-06-22

---

## 📝 完整转写

这是 v1 时期的转录正文。

---

## 🎬 章节要点

- v1 要点甲

---

## 💡 金句 / 可复用素材

- "控制回撤比追求收益更重要"

---

## 🔗 相关链接

- [抖音原视频](https://www.douyin.com/video/7650813418760359203)
"""

    V1_INDEXED = """---
title: "v1未转录笔记"
video_id: "7650813418760359204"
source: "https://www.douyin.com/video/7650813418760359204"
author: "老李"
platform: douyin
date: 2026-06-23
likes: 10
evidence_level: indexed
status: indexed
tags: [抖音, 收藏]
---

# v1未转录笔记

## 📌 概述

> 待模型总结后填入

## 📝 完整转写

_待转录..._
"""

    # 连 ID 和 URL 都没有的旧笔记（platform 是唯一线索）
    NO_ID_NOTE = """---
title: "无ID笔记"
author: "某人"
platform: douyin
status: done
---

# 无ID笔记

## 📝 完整转写

内容 A
"""

    def _write(self, rel: str, content: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_fixture_really_has_no_source_id(self):
        """fixture 必须真的没有 source_id，否则测不到本轮问题"""
        from dyknow import frontmatter as fm

        meta, _ = fm.read_note(self._write("old.md", self.V1_DONE))
        self.assertNotIn("source_id", meta)
        self.assertNotIn("transcript_status", meta)
        self.assertNotIn("processing_status", meta)
        self.assertNotIn("category", meta)
        self.assertEqual(meta.get("status"), "done")

    def test_recover_from_video_id(self):
        from dyknow import frontmatter as fm
        from dyknow.state import recover_source_id

        meta, _ = fm.read_note(self._write("old.md", self.V1_DONE))
        self.assertEqual(recover_source_id(meta), "7650813418760359203")

    def test_recover_from_url_only(self):
        from dyknow.state import recover_source_id

        meta = {"source": "https://www.douyin.com/video/7650813418760359203"}
        self.assertEqual(recover_source_id(meta), "7650813418760359203")

    def test_never_fabricates_id(self):
        from dyknow.state import recover_source_id

        self.assertEqual(recover_source_id({}), "")
        self.assertEqual(recover_source_id({"title": "普通笔记"}), "")
        self.assertEqual(recover_source_id({"author": "某人"}), "")

    def test_v1_done_note_still_indexed(self):
        """旧 done 笔记不得因为没有 source_id 而从索引消失"""
        from dyknow.indexer import rebuild_index
        from dyknow.layout import INDEX_FILENAME

        self._write("未分类/v1旧笔记.md", self.V1_DONE)
        result = rebuild_index(self.root)
        st = result["stats"]
        self.assertEqual(st["total"], 1, "无 source_id 的 v1 done 笔记必须仍被索引")
        self.assertEqual(st["processed"], 1)

        text = (self.root / INDEX_FILENAME).read_text(encoding="utf-8")
        self.assertIn("v1旧笔记", text)
        # 旧版正文结构里的摘要（## 📌 概述 的引用行）必须被读到
        self.assertIn("用分仓法把回撤控制在 10% 以内", text)

    def test_v1_state_resolution(self):
        from dyknow import frontmatter as fm
        from dyknow.state import resolve_state

        meta, body = fm.read_note(self._write("a.md", self.V1_DONE))
        self.assertEqual(resolve_state(meta, body), ("done", "done"))

        meta2, body2 = fm.read_note(self._write("b.md", self.V1_INDEXED))
        self.assertEqual(resolve_state(meta2, body2), ("pending", "pending"))

    def test_recover_from_filename(self):
        """连 ID 字段都没有时，从文件名 `<标题>_<id>.md` 恢复"""
        from dyknow.state import recover_source_id_from_filename

        self.assertEqual(
            recover_source_id_from_filename("财富密码_7645323421118845563.md"),
            "7645323421118845563",
        )
        self.assertEqual(recover_source_id_from_filename("普通笔记.md"), "")
        self.assertEqual(recover_source_id_from_filename("doumark-lite源码说明.md"), "")

    def test_v1_note_with_id_only_in_filename_still_indexed(self):
        """ID 只出现在文件名里的旧笔记也不得从索引消失"""
        from dyknow.indexer import rebuild_index

        raw = (
            "---\ntitle: \"只有文件名有ID的旧笔记\"\n"
            "author: \"某人\"\nstatus: done\n---\n\n"
            "# 只有文件名有ID的旧笔记\n\n"
            "## 📝 完整转写\n\n文件名带 ID 的旧笔记转录正文。\n"
        )
        self._write("未分类/某笔记_7645323421118845563.md", raw)
        result = rebuild_index(self.root)
        self.assertEqual(result["stats"]["total"], 1)
        self.assertEqual(result["stats"]["processed"], 1)

    def test_plain_markdown_still_excluded(self):
        """用户自己的普通笔记不应被误当视频笔记计入索引"""
        from dyknow.indexer import rebuild_index

        (self.root / "我的读书笔记.md").write_text(
            "---\ntitle: 读书笔记\n---\n\n随手记的内容\n", encoding="utf-8"
        )
        result = rebuild_index(self.root)
        self.assertEqual(result["stats"]["total"], 0)
        self.assertTrue(any("非视频笔记" in w for w in result["warnings"]))

    def test_v1_note_without_id_is_not_deduplicated(self):
        """无可靠 ID 的旧笔记在归档时保守处理：两份内容不同都必须保留"""
        from dyknow.layout import PENDING_CLUSTER_DIR
        from dyknow.organizer import organize

        self._write("未处理/noid.md", self.NO_ID_NOTE.replace("内容 A", "内容 A"))
        self._write("未处理/更深/noid.md", self.NO_ID_NOTE.replace("内容 A", "内容 B"))

        organize(self.root)
        found = sorted(p.name for p in (self.root / PENDING_CLUSTER_DIR).glob("noid*.md"))
        self.assertEqual(len(found), 2, f"无可靠 ID 时不得去重: {found}")


# ═══════════════════════════════════════════════════════════════
# I. needs_review / pending / done 语义（v2.2 收口）
# ═══════════════════════════════════════════════════════════════


class TestNeedsReviewSemantics(BaseCase):
    """三态互斥；needs_review 不得被搬回未处理，也不得丢 category"""

    def test_pending_goes_to_unprocessed(self):
        """Test A：pending 仍进入未处理目录"""
        from dyknow.layout import UNPROCESSED_DIR
        from dyknow.organizer import organize

        make_note(self.root / "根" / "p.md", "6000000000000000001", "待处理笔记",
                  processing_status="pending")
        organize(self.root)
        self.assertTrue((self.root / UNPROCESSED_DIR / "p.md").exists())

    def test_needs_review_stays_in_place(self):
        """Test B：已分类的 needs_review 不被搬回未处理"""
        from dyknow.layout import UNPROCESSED_DIR
        from dyknow.organizer import Organizer

        note = self.root / "AI工具" / "example.md"
        make_note(note, "6000000000000000002", "需复核笔记",
                  category="AI工具", processing_status="needs_review")

        org = Organizer(self.root)
        plans, _ = org.plan()
        res = org.apply(plans)

        self.assertTrue(note.exists(), "文件应保持在 AI工具/example.md")
        self.assertFalse((self.root / UNPROCESSED_DIR / "example.md").exists())
        self.assertFalse((self.root / "example.md").exists())

        plan = next(p for p in plans if p.src == note)
        self.assertEqual(plan.action, "skip")
        self.assertIn("保持当前位置", plan.reason)
        self.assertTrue(all(m.src != note for m in res.moved))

    def test_stats_are_mutually_exclusive(self):
        """Test C：done / pending / needs_review 统计互斥"""
        from dyknow.indexer import IndexBuilder

        make_note(self.root / "根" / "d.md", "6000000000000000011", "done笔记",
                  category="AI工具", processing_status="done")
        make_note(self.root / "根" / "p.md", "6000000000000000012", "pending笔记",
                  processing_status="pending")
        make_note(self.root / "AI工具" / "r.md", "6000000000000000013", "复核笔记",
                  category="AI工具", processing_status="needs_review")

        b = IndexBuilder(self.root)
        b.scan()
        st = b.stats()
        self.assertEqual(st["total"], 3)
        self.assertEqual(st["processed"], 1)
        self.assertEqual(st["pending"], 1, "needs_review 不得被计入 pending")
        self.assertEqual(st["needs_review"], 1)

    def test_needs_review_keeps_category(self):
        """Test D：organize / index 后 category 与状态都不被改写"""
        from dyknow import frontmatter as fm
        from dyknow.indexer import rebuild_index
        from dyknow.organizer import organize

        note = self.root / "AI工具" / "keep.md"
        make_note(note, "6000000000000000021", "保留分类", category="AI工具",
                  processing_status="needs_review")

        organize(self.root)
        rebuild_index(self.root)

        meta, _ = fm.read_note(note)
        self.assertEqual(meta.get("category"), "AI工具")
        self.assertEqual(meta.get("processing_status"), "needs_review")


# ═══════════════════════════════════════════════════════════════
# J. 根索引 total（v2.2 收口）
# ═══════════════════════════════════════════════════════════════


class TestRootIndexTotal(BaseCase):
    """根索引 frontmatter 的 total 必须是全部视频笔记数，而不是 processed"""

    def _read_total(self) -> int | None:
        import re

        from dyknow.layout import INDEX_FILENAME

        text = (self.root / INDEX_FILENAME).read_text(encoding="utf-8")
        m = re.search(r"^total:\s*(\d+)\s*$", text, re.MULTILINE)
        return int(m.group(1)) if m else None

    def test_total_counts_all_notes(self):
        from dyknow.indexer import rebuild_index
        from dyknow.layout import INDEX_FILENAME

        for i in range(3):
            make_note(self.root / "AI工具" / f"a{i}.md", f"610000000000000000{i}",
                      f"已完成{i}", category="AI工具", processing_status="done")
        make_note(self.root / "根" / "p.md", "6100000000000000099", "待处理",
                  processing_status="pending")

        rebuild_index(self.root)

        self.assertEqual(self._read_total(), 4, "total 应为 4，而不是 processed 的 3")
        text = (self.root / INDEX_FILENAME).read_text(encoding="utf-8")
        self.assertIn("已处理 3", text)
        self.assertIn("待处理 1", text)

    def test_total_with_needs_review(self):
        from dyknow.indexer import IndexBuilder, rebuild_index

        make_note(self.root / "AI工具" / "d1.md", "6200000000000000001", "done1",
                  category="AI工具", processing_status="done")
        make_note(self.root / "AI工具" / "d2.md", "6200000000000000002", "done2",
                  category="AI工具", processing_status="done")
        make_note(self.root / "根" / "p.md", "6200000000000000003", "pending",
                  processing_status="pending")
        make_note(self.root / "AI工具" / "r.md", "6200000000000000004", "review",
                  category="AI工具", processing_status="needs_review")

        rebuild_index(self.root)
        self.assertEqual(self._read_total(), 4)

        b = IndexBuilder(self.root)
        b.scan()
        st = b.stats()
        self.assertEqual(
            (st["total"], st["processed"], st["pending"], st["needs_review"]),
            (4, 2, 1, 1),
        )


# ═══════════════════════════════════════════════════════════════
# K. source_id 恢复收紧（v2.2 收口）
# ═══════════════════════════════════════════════════════════════


class TestSourceIdStrictness(BaseCase):
    """普通 Markdown 不得因为长数字被误识别成抖音视频笔记"""

    def test_legit_douyin_url_recovers(self):
        """Test A"""
        from dyknow.state import is_video_note, recover_source_id

        meta = {"source": "https://www.douyin.com/video/7650813418760359203"}
        self.assertEqual(recover_source_id(meta), "7650813418760359203")
        self.assertTrue(is_video_note(meta))

    def test_non_douyin_url_long_digits_not_recovered(self):
        """Test B"""
        from dyknow.state import is_video_note, recover_source_id

        meta = {
            "title": "订单记录",
            "source": "https://example.com/order/7650813418760359203",
        }
        self.assertEqual(recover_source_id(meta), "")
        self.assertFalse(is_video_note(meta))

    def test_forged_hosts_rejected(self):
        """Test C：伪 host / 参数里夹带 douyin.com 都不得通过"""
        from dyknow.state import (
            is_video_note,
            recover_source_id,
            trusted_douyin_host,
        )

        bad_urls = (
            "https://notdouyin.com/video/7650813418760359203",
            "https://example.com/?next=https://www.douyin.com/video/7650813418760359203",
            "https://evil-example.com/?redirect=douyin.com",
            "https://douyin.com.evil.com/video/7650813418760359203",
        )
        for bad in bad_urls:
            self.assertEqual(trusted_douyin_host(bad), "", bad)
            self.assertEqual(recover_source_id({"source": bad}), "", bad)
            self.assertFalse(is_video_note({"source": bad}), bad)

    def test_legit_subdomains_pass(self):
        from dyknow.state import trusted_douyin_host

        for good in (
            "https://www.douyin.com/video/7650813418760359203",
            "https://v.douyin.com/abc123/",
            "https://www.iesdouyin.com/share/video/7650813418760359203",
            "douyin.com/video/1",
        ):
            self.assertTrue(trusted_douyin_host(good), good)

    def test_filename_trailing_id_recovers(self):
        """Test D：v1 文件名尾部 aweme_id 仍可恢复"""
        from dyknow.state import recover_source_id_from_filename

        self.assertEqual(
            recover_source_id_from_filename("多Agent协作编排实战_7650813418760359203.md"),
            "7650813418760359203",
        )
        self.assertEqual(
            recover_source_id_from_filename("我的视频_7650813418760359203.md"),
            "7650813418760359203",
        )

    def test_filename_middle_digits_not_recovered(self):
        """Test E：文件名中间的长数字不得恢复"""
        from dyknow.state import recover_source_id_from_filename

        self.assertEqual(
            recover_source_id_from_filename("订单7650813418760359203记录.md"), ""
        )
        self.assertEqual(
            recover_source_id_from_filename("2026订单1234567890123456789记录.md"), ""
        )

    def test_plain_webpage_not_video_note(self):
        from dyknow.state import is_video_note

        self.assertFalse(is_video_note({
            "title": "普通网页",
            "url": "https://example.com/article/7650813418760359203",
        }))

    def test_explicit_id_fields_still_work(self):
        """显式 ID 字段继续可用（不破坏 v1 兼容）"""
        from dyknow.state import is_video_note, recover_source_id

        for key in ("source_id", "video_id", "aweme_id", "awemeId", "item_id", "itemId"):
            meta = {key: "7650813418760359203"}
            self.assertEqual(recover_source_id(meta), "7650813418760359203", key)
            self.assertTrue(is_video_note(meta), key)

    def test_short_numeric_id_not_recognized(self):
        """
        普通短数字 ID 不得被识别。

        `item_id` / `itemId` 是很通用的字段名，`item_id: 123` 这种普通
        Markdown 不能因为一个短数字就被当成抖音视频笔记。
        """
        from dyknow.state import is_video_note, recover_source_id

        meta = {"title": "我的普通项目", "item_id": "123"}
        self.assertEqual(recover_source_id(meta), "")
        self.assertFalse(is_video_note(meta))

        # 其他显式字段名同理，且 14 位（低于下界）也不能恢复
        for key in ("source_id", "video_id", "aweme_id", "awemeId", "item_id", "itemId"):
            for short in ("123", "0", "12345", "12345678901234"):
                self.assertEqual(recover_source_id({key: short}), "", f"{key}={short}")
            self.assertFalse(is_video_note({key: "123"}), key)

    def test_long_numeric_id_still_works(self):
        """合法长 ID 保持兼容（长度区间下界 15 位、上界 25 位）"""
        from dyknow.state import is_video_note, recover_source_id

        # 19 位（真实 aweme_id）
        meta = {"item_id": "7650813418760359203"}
        self.assertEqual(recover_source_id(meta), "7650813418760359203")
        self.assertTrue(is_video_note(meta))

        # 边界：15 位与 25 位均应接受
        self.assertEqual(recover_source_id({"video_id": "1" * 15}), "1" * 15)
        self.assertEqual(recover_source_id({"video_id": "1" * 25}), "1" * 25)
        # 边界外：14 位与 26 位均不接受
        self.assertEqual(recover_source_id({"video_id": "1" * 14}), "")
        self.assertEqual(recover_source_id({"video_id": "1" * 26}), "")

    def test_platform_douyin_still_recognized(self):
        from dyknow.state import is_video_note

        self.assertTrue(is_video_note({"platform": "douyin"}))
        self.assertTrue(is_video_note({"platform": "Douyin"}))


# ═══════════════════════════════════════════════════════════════
# L. 输出目录持久化（v2.3：首次确认一次，后续记住）
# ═══════════════════════════════════════════════════════════════


class TestConfigPersistence(BaseCase):
    """输出目录优先级：--output > DYKNOW_OUTPUT_DIR > data/config.json > cwd/抖音收藏"""

    def _cfg(self, data_dir=None):
        from dyknow.config import Config

        return Config(data_dir=data_dir or (self.tmp / "_data_cfg"))

    def test_save_then_reload(self):
        """保存后重新实例化仍能读到（等价于「下次运行自动记住」）"""
        target = self.tmp / "我的知识库"
        cfg = self._cfg()
        saved = cfg.save_output_dir(target)

        self.assertEqual(saved, target)
        self.assertTrue(cfg.config_path.exists())

        fresh = self._cfg()
        self.assertEqual(fresh.output_dir, target)
        self.assertIn("配置文件", fresh.output_dir_source())

    def test_relative_path_becomes_absolute(self):
        """相对路径按 cwd 展开为绝对路径，避免日后 cwd 变化导致漂移"""
        cfg = self._cfg()
        saved = cfg.save_output_dir("./相对知识库")
        self.assertTrue(saved.is_absolute())
        self.assertEqual(self._cfg().output_dir, saved)

    def test_runtime_override_wins_and_does_not_persist(self):
        cfg = self._cfg()
        cfg.save_output_dir(self.tmp / "保存的目录")

        override = self.tmp / "临时目录"
        cfg.output_dir = override
        self.assertEqual(cfg.output_dir, override)
        self.assertIn("运行时覆盖", cfg.output_dir_source())

        # 清除覆盖后回到配置文件里的值（说明覆盖没有写盘）
        cfg.output_dir = None
        self.assertEqual(cfg.output_dir, self.tmp / "保存的目录")

    def test_env_beats_config_file(self):
        from unittest import mock

        cfg = self._cfg()
        cfg.save_output_dir(self.tmp / "保存的目录")
        env_dir = self.tmp / "环境变量目录"

        with mock.patch.dict(os.environ, {"DYKNOW_OUTPUT_DIR": str(env_dir)}):
            self.assertEqual(cfg.output_dir, env_dir)
            self.assertIn("环境变量", cfg.output_dir_source())

    def test_config_file_beats_cwd_default(self):
        from unittest import mock

        cfg = self._cfg()
        target = self.tmp / "保存的目录"
        cfg.save_output_dir(target)

        with mock.patch.dict(os.environ, {"DYKNOW_OUTPUT_DIR": ""}):
            self.assertEqual(cfg.output_dir, target)

    def test_corrupt_config_file_falls_back(self):
        """配置文件损坏时不得崩溃，应回退到默认值"""
        from unittest import mock

        cfg = self._cfg()
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.config_path.write_text("{ 这不是合法 json", encoding="utf-8")

        self.assertEqual(cfg.read_config_file(), {})
        with mock.patch.dict(os.environ, {"DYKNOW_OUTPUT_DIR": ""}):
            self.assertEqual(cfg.output_dir, Path.cwd() / "抖音收藏")
            self.assertIn("默认值", cfg.output_dir_source())

    def test_save_preserves_other_keys(self):
        """写入输出目录不得覆盖配置文件里的其他键"""
        cfg = self._cfg()
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.config_path.write_text('{"other": "keep"}\n', encoding="utf-8")

        cfg.save_output_dir(self.tmp / "目录")

        data = cfg.read_config_file()
        self.assertEqual(data.get("other"), "keep")
        self.assertIn("output_dir", data)

    def test_cli_config_set_output_roundtrip(self):
        """CLI 真实执行：设置后再查询应显示该目录"""
        data_dir = self.tmp / "_data_cli"
        target = self.tmp / "CLI知识库"
        env = dict(os.environ, DYKNOW_DATA_DIR=str(data_dir))
        env.pop("DYKNOW_OUTPUT_DIR", None)

        r = subprocess.run(
            [sys.executable, "-m", "dyknow", "config", "--set-output", str(target)],
            cwd=str(PROJECT_ROOT), capture_output=True, timeout=60, env=env,
        )
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertTrue((data_dir / "config.json").exists())

        r2 = subprocess.run(
            [sys.executable, "-m", "dyknow", "config"],
            cwd=str(PROJECT_ROOT), capture_output=True, timeout=60, env=env,
        )
        out = (r2.stdout or b"").decode("utf-8", "replace")
        self.assertIn(str(target), out)

    def test_output_dir_flows_into_generated_note(self):
        """配置文件里的目录真的会成为笔记落盘位置"""
        from unittest import mock

        from dyknow import config as cfg_mod
        from dyknow.generator import generate_note

        target = self.tmp / "持久化知识库"
        cfg_mod.config.data_dir = self.tmp / "_data_flow"
        # 清除 setUp 里设置的运行时覆盖，让配置文件成为实际生效来源
        cfg_mod.config.output_dir = None
        cfg_mod.config.save_output_dir(target)

        with mock.patch.dict(os.environ, {"DYKNOW_OUTPUT_DIR": ""}):
            p = generate_note(title="落盘位置测试", aweme_id="9000000000000000001")

        self.assertTrue(str(p).startswith(str(target)), p)
        self.assertTrue(p.exists())

    def test_sync_transcribe_browse_accept_output_flag(self):
        """sync / transcribe / browse 现在都可接受 --output（此前只能靠环境变量）"""
        for cmd in ("sync", "transcribe", "browse"):
            r = subprocess.run(
                [sys.executable, "-m", "dyknow", cmd, "--help"],
                cwd=str(PROJECT_ROOT), capture_output=True, timeout=60,
            )
            out = (r.stdout or b"").decode("utf-8", "replace")
            self.assertIn("--output", out, f"{cmd} 缺少 --output")


# ═══════════════════════════════════════════════════════════════

def run():
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite).wasSuccessful()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
