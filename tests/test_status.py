"""状态显示必须以笔记反映 AI 的处理结果，而不是陈旧的数据库字段。"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dyknow.config import config
from dyknow.db import SyncDB
from dyknow.main import cmd_status


class TestStatusNotes(unittest.TestCase):
    def test_completed_note_overrides_stale_database_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = SyncDB(root / "state.db")
            db.insert("1234567890123456789", title="视频", status="transcribed")
            (root / "note.md").write_text(
                '---\nsource_id: "1234567890123456789"\nplatform: douyin\n'
                'transcript_status: done\nprocessing_status: done\n---\n'
                '# 视频\n\n## 📝 完整转写\n内容\n', encoding="utf-8")
            output = io.StringIO()
            with patch.object(config, "_output_override", root), \
                    patch("dyknow.main.get_db", return_value=db), \
                    patch("dyknow.login.load_cookies", return_value=None), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(cmd_status(None), 0)
            text = output.getvalue()
            self.assertIn("实际笔记扫描", text)
            self.assertRegex(text, r"done\s+1")
            self.assertRegex(text, r"pending\s+0")

    def test_sync_transcribes_only_new_ids_and_records_handoff(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from dyknow.syncer import Syncer
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = SyncDB(root / "state.db")
            db.insert("old", title="旧任务", status="pending_index")
            item = SimpleNamespace(aweme_id="new", title="新视频", author="作者")
            def index(items):
                db.insert("new", title="新视频", note_path=str(root / "new.md"))
                return 1
            with patch.object(config, "data_dir", root), \
                    patch.object(config, "_output_override", root), \
                    patch("dyknow.syncer.get_db", return_value=db), \
                    patch("dyknow.syncer.load_cookies", return_value=[{}]), \
                    patch("dyknow.syncer.scrape_fetch", new=AsyncMock(return_value=[item])), \
                    patch.object(Syncer, "_index_items", side_effect=index), \
                    patch.object(Syncer, "_phase2_transcribe", return_value=0) as transcribe, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(Syncer().sync(transcribe=True), 0)
            transcribe.assert_called_once_with(source_ids={"new"})
            ids = list((root / "runs").glob("*/new.ids"))
            self.assertEqual(ids[0].read_text(encoding="utf-8"), "new\n")
            self.assertTrue((ids[0].parent / "更新清单.md").is_file())
            self.assertEqual(db.get_by_status("pending_index")[0]["aweme_id"], "old")
            db.close()

    def test_missing_note_cannot_be_reported_as_transcribed(self):
        from dyknow.syncer import Syncer
        with tempfile.TemporaryDirectory() as tmp:
            db = SyncDB(Path(tmp) / "state.db")
            video_id = "1234567890123456789"
            db.insert(video_id, title="视频")
            with patch("dyknow.syncer.get_db", return_value=db), \
                    patch("dyknow.syncer.download") as download:
                ok, info = Syncer()._process_one(
                    video_id, "https://example.com/video", Path(tmp) / "missing.md",
                    "pending_index")
            self.assertFalse(ok)
            self.assertIn("笔记文件缺失", info["error"])
            download.assert_not_called()
            self.assertEqual(db.get_by_status("failed")[0]["aweme_id"], video_id)
            db.close()

    def test_organize_subset_preserves_unrelated_notes(self):
        from dyknow.organizer import Organizer
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for video_id in ('1234567890123456789', '2234567890123456789'):
                (root / f'{video_id}.md').write_text(
                    f'---\nsource_id: "{video_id}"\nplatform: douyin\n'
                    'transcript_status: done\nprocessing_status: done\n---\n'
                    '# 视频\n\n## 📝 完整转写\n内容\n', encoding='utf-8')
            org = Organizer(root, dry_run=True, source_ids={'1234567890123456789'})
            plans, _ = org.plan()
            self.assertEqual([p.source_id for p in plans], ['1234567890123456789'])
            self.assertTrue((root / '2234567890123456789.md').exists())

    def test_nested_taxonomy_resolves_and_survives_roundtrip(self):
        from dyknow.taxonomy import Taxonomy
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = '自媒体/AI视频制作/剪辑与自动化'
            tax = Taxonomy(root)
            self.assertEqual(tax.register(path, aliases=['口播剪辑']), path)
            tax.save()
            again = Taxonomy(root).load()
            self.assertEqual(again.resolve(path), path)
            self.assertEqual(again.resolve('口播剪辑'), path)
            self.assertIn(path, again.all_paths())
            again.register(path, aliases=['自动剪辑'])
            again.save()
            final = Taxonomy(root).load()
            self.assertEqual(final.resolve('自动剪辑'), path)
            self.assertEqual(len(final.categories[0].children[0].children), 1)
