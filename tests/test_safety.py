"""工作树共享状态与笔记保护的回归测试。"""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from dyknow.config import config, default_data_dir
from dyknow.db import SyncDB
from dyknow.generator import generate_note, update_transcript
from dyknow import frontmatter as fm

class TestSafety(unittest.TestCase):
    def test_worktree_uses_main_data_and_model(self):
        from dyknow.config import Config
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            common = root / "main" / ".git"
            admin = common / "worktrees" / "child"
            admin.mkdir(parents=True)
            (admin / "commondir").write_text("../..")
            child = root / "child"
            child.mkdir()
            (child / ".git").write_text(f"gitdir: {admin}")
            self.assertEqual(default_data_dir(child), common.parent / "data")
            cfg = Config(data_dir=default_data_dir(child))
            self.assertEqual(Path(cfg.ggml_model_path), common.parent / "data/models/ggml-tiny.bin")
            with patch.dict(os.environ, {"DYKNOW_DATA_DIR": str(root / "override")}):
                self.assertEqual(default_data_dir(child), root / "override")

    def test_full_refresh_preserves_archived_note_and_database(self):
        from dyknow.syncer import Syncer
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            note = root / "archived.md"
            note.write_text("用户整理正文", encoding="utf-8")
            db = SyncDB(root / "state.db")
            db.insert("123", title="旧标题", status="transcribed", note_path=str(note))
            db.update_status("123", "transcribed", processing_status="done", transcript_hash="abc")
            with patch("dyknow.syncer.get_db", return_value=db), patch("dyknow.syncer.generate_note") as generate:
                count = Syncer()._index_items([SimpleNamespace(aweme_id="123", title="新标题", author="作者", cover_url="", video_url="new", duration=42)])
            generate.assert_not_called()
            row = db.conn.execute("SELECT * FROM sync_log WHERE aweme_id='123'").fetchone()
            self.assertEqual(count, 0)
            self.assertEqual(row["title"], "新标题")
            self.assertEqual(row["status"], "transcribed")
            self.assertEqual(row["processing_status"], "done")
            self.assertEqual(row["transcript_hash"], "abc")
            self.assertEqual(row["note_path"], str(note))
            self.assertEqual(note.read_text(encoding="utf-8"), "用户整理正文")
            db.close()

    def test_regeneration_and_retranscription_preserve_ai_sections(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "_output_override", Path(tmp)):
            note = generate_note("旧标题", aweme_id="123", transcript="旧转写")
            text = fm.set_field(note.read_text(encoding="utf-8"), "processing_status", "done")
            meta, _ = fm.parse_frontmatter(text)
            text = fm.set_field(text, "summarized_from_hash", meta["transcript_hash"])
            text = fm.set_field(text, "custom", "自定义字段")
            text = fm.replace_section(text, "🤖 AI 摘要", "其他 AI 的摘要")
            note.write_text(text, encoding="utf-8")
            archived = Path(tmp) / "分类" / note.name
            archived.parent.mkdir()
            note.rename(archived)
            self.assertEqual(generate_note("新标题", aweme_id="123"), archived)
            self.assertEqual(archived.read_text(encoding="utf-8"), text)
            update_transcript(archived, "新转写")
            current = archived.read_text(encoding="utf-8")
            self.assertIn("其他 AI 的摘要", current)
            self.assertIn("自定义字段", current)
            self.assertIn("needs_review", current)
            update_transcript(archived, "")
            self.assertIn("新转写", archived.read_text(encoding="utf-8"))

    def test_login_in_existing_context_does_not_navigate_or_click(self):
        from dyknow.login import login_in_context
        context = MagicMock()
        context.cookies = AsyncMock(return_value=[{"name":"sessionid"}, {"name":"uid_tt"}])
        page = MagicMock()
        page.goto = AsyncMock()
        with patch("dyknow.login.save_cookies") as save:
            asyncio.run(login_in_context(context, page))
        page.goto.assert_not_called()
        page.locator.assert_not_called()
        save.assert_called_once()

    def test_login_reuses_saved_cookie_without_launching_browser(self):
        from dyknow.login import login
        cookies = [{"name": "sessionid"}, {"name": "uid_tt"}]
        with patch("dyknow.login.load_cookies", return_value=cookies), patch("dyknow.login.async_playwright") as browser:
            self.assertEqual(asyncio.run(login()), cookies)
        browser.assert_not_called()

    def test_failed_write_is_not_success_or_cache_deletion(self):
        from dyknow.syncer import Syncer
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            note = root / "note.md"
            note.write_text("原内容", encoding="utf-8")
            audio = root / "audio.wav"
            audio.touch()
            db = SyncDB(root / "state.db")
            db.insert("123", note_path=str(note), status="pending_transcribe")
            db.update_status("123", "pending_transcribe", audio_path=str(audio))
            with patch("dyknow.syncer.get_db", return_value=db), patch("dyknow.syncer.transcribe", return_value="转录"), patch("dyknow.syncer.update_transcript", return_value=False):
                ok, info = Syncer()._process_one("123", "", note, "pending_transcribe")
            self.assertFalse(ok)
            self.assertIn("写入", info["error"])
            self.assertTrue(audio.exists())
            self.assertEqual(db.get_by_status("failed")[0]["aweme_id"], "123")
            db.close()
