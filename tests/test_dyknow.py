"""
DyKnow 完整测试套件

运行:
    cd DyKnow项目目录
    .venv/Scripts/python.exe tests/test_dyknow.py
    .venv/Scripts/python.exe -m pytest tests/test_dyknow.py -v
"""

import os
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


# ═══════════════════════════════════════════════════════════════
# 基础模块测试
# ═══════════════════════════════════════════════════════════════

class TestConfig(unittest.TestCase):
    """配置模块"""

    def test_defaults(self):
        from dyknow.config import Config
        c = Config()
        self.assertIn("cookies.json", str(c.cookie_path))
        self.assertIn("dyknow.db", str(c.db_path))

    def test_ensure_dirs(self):
        from dyknow.config import config
        config.ensure_dirs()
        self.assertTrue(config.data_dir.exists())
        self.assertTrue(config.video_cache_dir.exists())

    def test_output_dir(self):
        from dyknow.config import config
        self.assertIsNotNone(config.output_dir)


class TestDB(unittest.TestCase):
    """数据库模块"""

    def setUp(self):
        from dyknow.db import SyncDB
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = SyncDB(Path(self.tmp))

    def tearDown(self):
        self.db.close()
        try:
            os.unlink(self.tmp)
        except Exception:
            pass

    def test_insert_exists(self):
        self.db.insert("id_001", title="测试", status="indexed")
        self.assertTrue(self.db.exists("id_001"))
        self.assertFalse(self.db.exists("id_999"))

    def test_get_new_ids(self):
        self.db.insert("id_001", status="indexed")
        self.db.insert("id_002", status="indexed")
        new = self.db.get_new_ids(["id_001", "id_002", "id_003"])
        self.assertEqual(new, ["id_003"])

    def test_status_flow(self):
        self.db.insert("id_f", status="indexed")
        self.assertEqual(self.db.count_by_status("indexed"), 1)
        self.db.update_status("id_f", "downloaded")
        self.assertEqual(self.db.count_by_status("downloaded"), 1)
        self.assertEqual(self.db.count_by_status("indexed"), 0)
        self.db.update_status("id_f", "done")
        self.assertEqual(self.db.count_by_status("done"), 1)

    def test_get_by_status(self):
        self.db.insert("a", title="A", status="indexed")
        self.db.insert("b", title="B", status="indexed")
        self.assertEqual(len(self.db.get_by_status("indexed")), 2)

    def test_meta(self):
        self.db.set_meta("last_sync", "2025-07-05")
        self.assertEqual(self.db.get_meta("last_sync"), "2025-07-05")
        self.assertIsNone(self.db.get_meta("nonexistent"))

    def test_total_count(self):
        self.db.insert("x", status="indexed")
        self.db.insert("y", status="done")
        self.assertEqual(self.db.total_count(), 2)


class TestParser(unittest.TestCase):
    """单视频解析器"""

    def test_extract_url_short(self):
        from dyknow.parser import extract_url
        url = extract_url("看看 https://v.douyin.com/abc123/ 有趣")
        self.assertIsNotNone(url)
        self.assertIn("v.douyin.com", url)

    def test_extract_url_full(self):
        from dyknow.parser import extract_url
        url = extract_url("https://www.douyin.com/video/7234567890123456789")
        self.assertIn("douyin.com/video/", url)

    def test_extract_url_none(self):
        from dyknow.parser import extract_url
        self.assertIsNone(extract_url("没有链接"))

    def test_extract_video_id(self):
        from dyknow.parser import extract_video_id
        vid = extract_video_id("https://www.douyin.com/video/7234567890123456789")
        self.assertEqual(vid, "7234567890123456789")

    def test_real_share_text(self):
        """模拟真实抖音分享文案"""
        from dyknow.parser import extract_url
        text = "7.64 03/20 :2pm A@g.bN cAT:/ 我试着用本地ComfyUI生成了一段视频# 日常分享  https://v.douyin.com/meu8KD02EPk/ 复制此链接，打开Dou音搜索，直接观看视频！"
        url = extract_url(text)
        self.assertIsNotNone(url)
        self.assertIn("v.douyin.com", url)


class TestGenerator(unittest.TestCase):
    """笔记生成器"""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_safe_filename(self):
        from dyknow.generator import safe_filename
        self.assertEqual(safe_filename('hello "world"'), "hello 'world'")

    def test_safe_filename_forbidden_chars(self):
        """Windows 禁用字符：\ / : * ? " < > | 均应被替换"""
        from dyknow.generator import safe_filename
        # 反斜杠和正斜杠 → 下划线
        self.assertEqual(safe_filename("a\\b/c"), "a_b_c")
        # 冒号 → 下划线
        self.assertEqual(safe_filename("a:b"), "a_b")
        # 星号 → 下划线
        self.assertEqual(safe_filename("a*b"), "a_b")
        # 问号 → 下划线
        self.assertEqual(safe_filename("a?b"), "a_b")
        # 双引号 → 单引号
        self.assertEqual(safe_filename('a"b'), "a'b")
        # 尖括号 → 下划线
        self.assertEqual(safe_filename("a<b>c"), "a_b_c")
        # 竖线 → 下划线
        self.assertEqual(safe_filename("a|b"), "a_b")

    def test_safe_filename_control_chars(self):
        """控制字符应被移除"""
        from dyknow.generator import safe_filename
        self.assertEqual(safe_filename("hello\x00world"), "helloworld")
        self.assertEqual(safe_filename("test\x1f\x7fend"), "testend")

    def test_safe_filename_leading_trailing(self):
        """首尾空格和句点应被去除"""
        from dyknow.generator import safe_filename
        self.assertEqual(safe_filename("  hello  "), "hello")
        self.assertEqual(safe_filename("...hello..."), "hello")

    def test_safe_filename_reserved_names(self):
        """Windows 保留名应追加下划线避免冲突"""
        from dyknow.generator import safe_filename
        self.assertEqual(safe_filename("CON"), "CON_")
        self.assertEqual(safe_filename("PRN"), "PRN_")
        self.assertEqual(safe_filename("AUX"), "AUX_")
        self.assertEqual(safe_filename("NUL"), "NUL_")
        self.assertEqual(safe_filename("COM1"), "COM1_")
        self.assertEqual(safe_filename("COM9"), "COM9_")
        self.assertEqual(safe_filename("LPT1"), "LPT1_")
        self.assertEqual(safe_filename("LPT9"), "LPT9_")
        # 不区分大小写
        self.assertEqual(safe_filename("con"), "con_")
        self.assertEqual(safe_filename("nul"), "nul_")
        self.assertEqual(safe_filename("com3"), "com3_")

    def test_safe_filename_empty(self):
        """空标题返回占位符"""
        from dyknow.generator import safe_filename
        self.assertEqual(safe_filename(""), "_")
        self.assertEqual(safe_filename("   "), "_")  # 多空格压缩为单个 _
        # 纯禁用字符的结果
        result = safe_filename("<>:|?*")
        self.assertTrue(len(result) > 0)
        self.assertTrue(all(c not in r'\/:*?"<>|' for c in result))

    def test_safe_filename_brackets(self):
        """方括号转为圆括号（Obsidian 兼容）"""
        from dyknow.generator import safe_filename
        self.assertEqual(safe_filename("标题[重要]"), "标题(重要)")

    def test_safe_filename_newlines(self):
        """换行符转为空格"""
        from dyknow.generator import safe_filename
        self.assertEqual(safe_filename("第一行\n第二行\r第三行"), "第一行 第二行 第三行")

    def test_safe_filename_truncation(self):
        """长标题应截断到 max_len"""
        from dyknow.generator import safe_filename
        long_title = "A" * 100
        result = safe_filename(long_title, max_len=30)
        self.assertLessEqual(len(result), 30)

    def test_safe_filename_combined(self):
        """组合场景：多种特殊字符混合"""
        from dyknow.generator import safe_filename
        dirty = '视频"标题": 测试/文件\\名<第1集>?*|
CON'
        result = safe_filename(dirty)
        # 不包含任何 Windows 禁用字符
        for ch in r'\/:*?<>|':
            self.assertNotIn(ch, result, f"不应包含字符: {ch}")
        # 双引号被替换为单引号
        self.assertNotIn('"', result)
        # 结果非空
        self.assertTrue(len(result) > 0)

    def test_generate_note_with_transcript(self):
        from dyknow.generator import generate_note
        path = generate_note(
            title="测试视频",
            aweme_id="7234567890123456789",
            author="测试作者",
            duration=125000,
            likes=1000,
            comments=50,
            shares=200,
            transcript="这是转录内容。",
            output_dir=self.tmp,
        )
        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        self.assertIn("测试视频", content)
        self.assertIn("7234567890123456789", content)
        self.assertIn("02:05", content)
        self.assertIn("1,000", content)
        self.assertIn("这是转录内容", content)
        self.assertIn("测试作者", content)
        # 不应有 AI 摘要占位符
        self.assertNotIn("待生成", content)
        self.assertIn("status: done", content)

    def test_generate_note_without_transcript(self):
        from dyknow.generator import generate_note
        path = generate_note(
            title="无转录",
            aweme_id="7234567890123456789",
            output_dir=self.tmp,
        )
        content = path.read_text(encoding="utf-8")
        self.assertIn("待转录...", content)
        self.assertIn("status: indexed", content)

    def test_update_transcript(self):
        from dyknow.generator import generate_note, update_transcript
        path = generate_note(
            title="待更新",
            aweme_id="7234567890123456789",
            output_dir=self.tmp,
        )
        update_transcript(path, "更新后的转录内容")
        content = path.read_text(encoding="utf-8")
        self.assertIn("更新后的转录内容", content)
        self.assertIn("status: transcribed", content)

    def test_update_status(self):
        from dyknow.generator import generate_note, update_status
        path = generate_note(
            title="状态测试",
            aweme_id="7234567890123456789",
            output_dir=self.tmp,
        )
        update_status(path, "done")
        self.assertIn("status: done", path.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════
# Scraper 数据类测试
# ═══════════════════════════════════════════════════════════════

class TestScraperItem(unittest.TestCase):
    """FavoriteItem 解析"""

    def test_from_aweme_basic(self):
        from dyknow.scraper import FavoriteItem
        aweme = {
            "aweme_id": "7234567890123456789",
            "desc": "测试标题",
            "duration": 60000,
            "author": {"nickname": "作者名", "sec_uid": "uid123"},
            "statistics": {"digg_count": 100, "comment_count": 10, "share_count": 5},
            "video": {
                "play_addr": {"url_list": ["http://video.url"]},
                "cover": {"url_list": ["http://cover.url"]},
            },
        }
        item = FavoriteItem.from_aweme(aweme)
        self.assertEqual(item.aweme_id, "7234567890123456789")
        self.assertEqual(item.title, "测试标题")
        self.assertEqual(item.author, "作者名")
        self.assertEqual(item.likes, 100)
        self.assertEqual(item.video_url, "http://video.url")

    def test_from_aweme_minimal(self):
        from dyknow.scraper import FavoriteItem
        item = FavoriteItem.from_aweme({"aweme_id": "123"})
        self.assertEqual(item.aweme_id, "123")
        self.assertEqual(item.title, "无标题")

    def test_from_aweme_none(self):
        from dyknow.scraper import FavoriteItem
        self.assertIsNone(FavoriteItem.from_aweme({}))


# ═══════════════════════════════════════════════════════════════
# 集成测试（需要 Cookie）
# ═══════════════════════════════════════════════════════════════

class TestLogin(unittest.TestCase):
    """登录状态"""

    def test_cookies_exist(self):
        from dyknow.login import load_cookies
        cookies = load_cookies()
        if not cookies:
            self.skipTest("未登录，跳过")
        self.assertIsInstance(cookies, list)
        self.assertTrue(len(cookies) > 0)


class TestCLI(unittest.TestCase):
    """CLI 命令"""

    def test_help(self):
        import subprocess
        r = subprocess.run(
            [sys.executable, "-m", "dyknow", "--help"],
            cwd=str(PROJECT_ROOT), capture_output=True, timeout=10,
        )
        self.assertEqual(r.returncode, 0)

    def test_status(self):
        import subprocess
        r = subprocess.run(
            [sys.executable, "-m", "dyknow", "status"],
            cwd=str(PROJECT_ROOT), capture_output=True, timeout=10,
        )
        self.assertEqual(r.returncode, 0)

    def test_parse_no_url(self):
        import subprocess
        r = subprocess.run(
            [sys.executable, "-m", "dyknow", "parse", "无链接文本"],
            cwd=str(PROJECT_ROOT), capture_output=True, timeout=10,
        )
        self.assertNotEqual(r.returncode, 0)

    def test_parse_with_url(self):
        """真实链接解析（需要网络，可能较慢）"""
        import subprocess
        try:
            r = subprocess.run(
                [sys.executable, "-m", "dyknow", "parse", "--no-transcribe",
                 "7.64 03/20 https://v.douyin.com/meu8KD02EPk/"],
                cwd=str(PROJECT_ROOT), capture_output=True, timeout=30,
            )
            print(f"\n  parse exit code: {r.returncode}")
        except subprocess.TimeoutExpired:
            self.skipTest("parse 超时，链接可能已失效")

    def test_login_command_exists(self):
        import subprocess
        r = subprocess.run(
            [sys.executable, "-m", "dyknow", "login", "--help"],
            cwd=str(PROJECT_ROOT), capture_output=True, timeout=10,
        )
        self.assertEqual(r.returncode, 0)

    def test_sync_help(self):
        import subprocess
        r = subprocess.run(
            [sys.executable, "-m", "dyknow", "sync", "--help"],
            cwd=str(PROJECT_ROOT), capture_output=True, timeout=10,
        )
        self.assertEqual(r.returncode, 0)


# ═══════════════════════════════════════════════════════════════
# 运行
# ═══════════════════════════════════════════════════════════════

def run():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 基础测试（不依赖网络/Cookie）
    for tc in [TestConfig, TestDB, TestParser, TestGenerator, TestScraperItem]:
        suite.addTests(loader.loadTestsFromTestCase(tc))

    runner = unittest.TextTestRunner(verbosity=2)
    print("\n" + "=" * 60)
    print("基础测试")
    print("=" * 60)
    result = runner.run(suite)

    # 集成测试（需要 Cookie/网络）
    print("\n" + "=" * 60)
    print("集成测试（需要 Cookie 和网络）")
    print("=" * 60)
    int_suite = unittest.TestSuite()
    for tc in [TestLogin, TestCLI]:
        int_suite.addTests(loader.loadTestsFromTestCase(tc))
    result2 = runner.run(int_suite)

    return result.wasSuccessful() and result2.wasSuccessful()


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
