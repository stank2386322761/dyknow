"""运行：python -m unittest discover -s tests -v"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from dyknow import asr, browser, cli, index, migrate, note  # noqa: E402
from dyknow.config import Config  # noqa: E402
from dyknow.douyin import (Comment, Video, find_links, parse_aweme, sec_uid_from_url,  # noqa: E402
                           video_id_from_url)
from dyknow.store import Store  # noqa: E402
from fake_douyin import FakeDouyin, make_aweme  # noqa: E402


def tmpdir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="dyknow-test-"))
    return d


def make_cfg(home: Path, base: str = "https://www.douyin.com", **values) -> Config:
    cfg = Config(home=home, values={"output_dir": str(home / "kb"), "browser": "chromium", "headless": True,
                                    "request_interval": 0, "comments": 3, **values})
    cfg.base_url = base
    return cfg


class FakeAsr:
    def __init__(self, interrupt_at: int = 0, empty_ids=(), fail_ids=()):
        self.calls = 0
        self.interrupt_at = interrupt_at
        self.empty_ids = set(empty_ids)
        self.fail_ids = set(fail_ids)
        self.note = ""
        self.device = "cuda"

    def describe(self):
        return "假转录"

    def transcribe(self, path):
        self.calls += 1
        if self.interrupt_at and self.calls == self.interrupt_at:
            raise KeyboardInterrupt
        vid = Path(path).stem
        if vid in self.fail_ids:
            raise asr.AsrError("模拟转录失败")
        if vid in self.empty_ids:
            return asr.Transcript(paragraphs=[(0.0, "嗯")])
        return asr.Transcript(paragraphs=[(0.0, "大家好，今天聊 Codex"), (35.0, "第二段内容")], language="zh")


@contextlib.contextmanager
def patched_asr(fake):
    orig = asr.make
    asr.make = lambda cfg, log=print: fake
    try:
        yield fake
    finally:
        asr.make = orig


def run_cli(argv, cfg) -> tuple[int, str, dict]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(argv, cfg=cfg)
    out = buf.getvalue()
    result_line = [ln for ln in out.splitlines() if ln.startswith("RESULT: ")][-1]
    return code, out, json.loads(result_line[len("RESULT: "):])


def login_cookie(cfg: Config) -> None:
    with browser.Douyin(cfg) as dy:
        dy.ctx.add_cookies([{"name": "sessionid", "value": "abc", "url": cfg.base_url,
                             "expires": int(time.time()) + 86400}])


# ── 纯函数 ────────────────────────────────────


class TestParsing(unittest.TestCase):
    def test_parse_video(self):
        v = parse_aweme(make_aweme(1, "http://x"))
        self.assertEqual(v.id, "7400000000000000001")
        self.assertEqual(v.title, "第1条视频：AI 工具实测")
        self.assertEqual(v.tags, ["AI工具", "效率"])
        self.assertEqual(v.author, "测试博主")
        self.assertFalse(v.is_image)
        self.assertIn("low=1", v.media_urls[0], "码率最低的地址应排在最前")

    def test_image_post_has_no_media(self):
        v = parse_aweme(make_aweme(3, "http://x", image=True))
        self.assertTrue(v.is_image)
        self.assertEqual(v.media_urls, [])

    def test_bad_input(self):
        self.assertIsNone(parse_aweme({"aweme_id": "abc"}))
        self.assertIsNone(parse_aweme("x"))

    def test_links(self):
        share = "7.43 复制打开抖音，看看【某某的作品】AI 做号实录 https://v.douyin.com/iRNBho5a/ Hxy:/ 02/11 m@q.Eu"
        self.assertEqual(find_links(share), ["https://v.douyin.com/iRNBho5a/"])
        self.assertEqual(video_id_from_url("https://www.douyin.com/video/7400000000000000001?x=1"),
                         "7400000000000000001")
        self.assertEqual(video_id_from_url("https://www.douyin.com/jingxuan?modal_id=7400000000000000002"),
                         "7400000000000000002")
        self.assertEqual(video_id_from_url("https://www.iesdouyin.com/share/video/7400000000000000003/"),
                         "7400000000000000003")
        self.assertEqual(find_links("就是这个 7400000000000000009 视频"), ["7400000000000000009"])
        self.assertEqual(sec_uid_from_url("https://www.douyin.com/user/MS4wLjABAAAA_x-y?from=1"), "MS4wLjABAAAA_x-y")
        self.assertEqual(sec_uid_from_url("https://www.douyin.com/user/self"), "")
        self.assertEqual(find_links("https://evil.com/?u=douyin.com/video/1"), [])


class TestNote(unittest.TestCase):
    def video(self, **kw):
        base = dict(id="7400000000000000001", desc="标题 #话题", author="- 小明: #AI \"引号\"",
                    published="2026-01-02", duration_ms=61000, likes=12345, tags=["话题"],
                    media_urls=["http://x"])
        base.update(kw)
        return Video(**base)

    def test_frontmatter_is_valid_yaml_with_weird_values(self):
        text = note.render(self.video(), note.load_template("general"), note.PENDING)
        fm = text.split("---")[1]
        data = yaml.safe_load(fm)
        self.assertEqual(data["author"], "- 小明: #AI \"引号\"")
        self.assertEqual(data["id"], "7400000000000000001")
        self.assertEqual(note.read_meta(text)["author"], "- 小明: #AI \"引号\"")
        self.assertEqual(note.get_status(text), note.PENDING)

    def test_blocks_do_not_touch_ai_text(self):
        text = note.render(self.video(), note.load_template("creator"), note.PENDING,
                           [Comment("说得好", 2310)])
        self.assertIn("- 👍2310 「说得好」", text)
        self.assertEqual(note.get_block(text, "desc"), "", "文案只是标题+话题时不重复显示")
        long = note.render(self.video(desc="标题\n第二行有更多内容 #话题"), note.load_template("general"), note.PENDING)
        self.assertIn("> 第二行有更多内容 #话题", note.get_block(long, "desc"))
        ai_written = text.replace("<!-- AI：一句话说清", "这是 AI 写的总结\n<!-- AI：一句话说清", 1)
        updated = note.replace_block(ai_written, "transcript", note.transcript_block([(0, "a"), (65, "b")]))
        self.assertIn("这是 AI 写的总结", updated)
        self.assertIn("**[01:05]** b", updated)
        self.assertNotIn("待转录", note.get_block(updated, "transcript"))
        self.assertEqual(updated.count("dyknow:transcript:start"), 1)
        self.assertIn("## 可借鉴", updated)

    def test_status_roundtrip_and_hand_edit(self):
        text = note.render(self.video(), note.load_template("general"), note.PENDING)
        text = note.set_status(text, note.TRANSCRIBED)
        self.assertEqual(note.get_status(text), "已转录")
        hand = text.replace('status: "已转录"', "status: 已整理")  # AI 手改成不带引号
        self.assertEqual(note.get_status(hand), "已整理")

    def test_first_ai_line(self):
        text = note.render(self.video(), note.load_template("general"), note.TRANSCRIBED)
        self.assertEqual(note.first_ai_line(text), "")
        text = text.replace("<!-- AI：一句话说清", "用 Codex 6 分钟做出 App\n<!-- AI：一句话说清", 1)
        self.assertEqual(note.first_ai_line(text), "用 Codex 6 分钟做出 App")

    def test_safe_filename(self):
        name = note.file_name(self.video(desc='a/b:c*?"<>|#^[x]  很长' * 5), "2026-10-14")
        self.assertTrue(name.endswith("_7400000000000000001.md"))
        for ch in '/\\:*?"<>|#^[]':
            self.assertNotIn(ch, name)

    def test_custom_template(self):
        d = tmpdir()
        t = d / "my.md"
        t.write_text("## 行动清单\n<!-- AI：能马上照做的步骤 -->\n", encoding="utf-8")
        self.assertIn("行动清单", note.load_template(str(t)))
        with self.assertRaises(FileNotFoundError):
            note.load_template("不存在")


class TestTranscriptFormat(unittest.TestCase):
    def test_smart_join(self):
        self.assertEqual(asr.smart_join("我用", "Codex"), "我用Codex")
        self.assertEqual(asr.smart_join("use", "Codex"), "use Codex")
        self.assertEqual(asr.smart_join("Hello.", "World"), "Hello. World")

    def test_grouping(self):
        first = "第一句" * 25
        segs = [(0, 2, first), (2, 4, "第二句"), (10, 12, "停顿很久之后"), (12, 14, "接着"), (13, 14, "短")]
        paras = asr.group_segments(segs)
        self.assertEqual(paras, [(0, first + "第二句"), (10, "停顿很久之后接着短")])
        # 太短的段落不会因为停顿被拆开
        self.assertEqual(len(asr.group_segments([(0, 1, "嗨"), (5, 6, "你好")])), 1)
        long = [(i * 2.0, i * 2.0 + 2, "字" * 30) for i in range(40)]
        self.assertTrue(all(len(t) <= 250 for _, t in asr.group_segments(long)))

    def test_empty_detection(self):
        self.assertTrue(asr.Transcript(paragraphs=[(0, "嗯…")]).is_empty)
        self.assertFalse(asr.Transcript(paragraphs=[(0, "今天聊一下")]).is_empty)

    def test_split_plain_without_punctuation(self):
        paras = asr.split_plain("没有标点的旧转录" * 100)
        self.assertGreater(len(paras), 3)
        self.assertTrue(all(len(t) <= 220 for _, t in paras))

    def test_split_plain(self):
        paras = asr.split_plain("第一句。" * 100, offset=300)
        self.assertEqual(paras[0][0], 300)
        self.assertIsNone(paras[1][0])

    def test_probe_error_hints(self):
        self.assertIn("cuDNN", asr._probe_error("Could not load library cudnn_ops64_9.dll", 1))
        self.assertIn("显存", asr._probe_error("CUDA failed: out of memory", 1))


class TestStoreIndexConfig(unittest.TestCase):
    def test_dedup_across_folders_and_index(self):
        d = tmpdir()
        store = Store(d / "kb")
        tpl = note.load_template("general")
        v = parse_aweme(make_aweme(1, "http://x"))
        p1, new1 = store.create(v, store.folder("搜索", "AI"), tpl)
        p2, new2 = Store(d / "kb").create(v, store.folder("收藏"), tpl)
        self.assertTrue(new1)
        self.assertFalse(new2)
        self.assertEqual(p1, p2)
        text = note.read(p1).replace("<!-- AI：一句话说清", "一句话总结在这\n<!-- AI：一句话说清", 1)
        note.write_atomic(p1, note.set_status(text, note.ORGANIZED))
        counts = index.rebuild(store)
        idx = (d / "kb" / "索引.md").read_text(encoding="utf-8")
        self.assertIn("一句话总结在这", idx)
        self.assertIn("## 搜索 / AI（1）", idx)
        self.assertEqual(counts["已整理"], 1)

    def test_config(self):
        d = tmpdir()
        cfg = Config(home=d)
        self.assertEqual(cfg.get("comments"), 5)
        cfg.set("comments", "8")
        cfg.set("headless", "true")
        from dyknow.config import load
        cfg2 = load(d)
        self.assertEqual(cfg2.get("comments"), 8)
        self.assertIs(cfg2.get("headless"), True)
        os.environ["DYKNOW_ASR"] = "cloud"
        try:
            self.assertEqual(cfg2.get("asr"), "cloud")
            self.assertIn("环境变量", cfg2.source_of("asr"))
        finally:
            del os.environ["DYKNOW_ASR"]


OLD_NOTE = """---
title: 学了一堆自媒体教程_第一条视频卡在哪
video_id: 7650813418760359203
source_id: 7650813418760359203
source: https://www.douyin.com/video/7650813418760359203
author: 叶孤城
duration: 03:42
likes: 12000
comments: 860
platform: douyin
date: 2026-09-01
transcript_status: done
processing_status: done
category: 自媒体运营
status: done
tags: [抖音, 收藏]
---

# 学了一堆自媒体教程_第一条视频卡在哪

## 🤖 AI 摘要

### 一句话总结
第一条视频卡住是因为选题没人搜，先用搜索框验证需求。

### 要点
- 用抖音搜索联想词验证选题
- 开头 3 秒给结果

### 金句
- "没人搜的选题，拍得再好也没人看"（转录原文）

---

## 📝 完整转写

大家好今天我们聊一聊为什么你的第一条视频发不出去。其实原因很简单。没人搜。

---

## 🎬 章节要点

_待模型填写_
"""


class TestMigrate(unittest.TestCase):
    def test_migrate_keeps_content_and_never_touches_old(self):
        d = tmpdir()
        old = d / "old" / "自媒体运营"
        old.mkdir(parents=True)
        f = old / "学了一堆自媒体教程_7650813418760359203.md"
        f.write_text(OLD_NOTE, encoding="utf-8")
        pending = old / "x_7650813418760359204.md"
        pending.write_text(OLD_NOTE.replace("7650813418760359203", "7650813418760359204")
                           .replace("大家好今天我们聊一聊为什么你的第一条视频发不出去。其实原因很简单。没人搜。", "_待转录..._")
                           .replace("transcript_status: done", "transcript_status: pending"), encoding="utf-8")
        (old / "我的随笔.md").write_text("# 随便写写", encoding="utf-8")
        before = {p: p.read_bytes() for p in old.iterdir()}
        store = Store(d / "kb")
        res = migrate.run(d / "old", store, note.load_template("general"))
        self.assertEqual(res.copied, 2)
        self.assertEqual(res.skipped_other, 1)
        self.assertEqual({p: p.read_bytes() for p in old.iterdir()}, before, "旧文件不能被修改")
        new = store.find("7650813418760359203")
        text = note.read(new)
        self.assertEqual(note.get_status(text), note.ORGANIZED)
        self.assertIn("第一条视频卡住是因为选题没人搜", text)
        self.assertIn("用抖音搜索联想词验证选题", text)
        self.assertIn("没人搜的选题", text)
        self.assertIn("其实原因很简单", note.get_block(text, "transcript"))
        self.assertIn("自媒体运营", note.read_meta(text)["tags"])
        order = [text.find(f"dyknow:{b}:start") for b in ("desc", "transcript", "comments")]
        self.assertEqual(order, sorted(order), "区块顺序：文案 → 转写 → 评论")
        self.assertIn("选题没人搜，先用搜索框验证需求。\n\n## 要点", text)
        self.assertEqual(note.get_status(note.read(store.find("7650813418760359204"))), note.PENDING)
        again = migrate.run(d / "old", Store(d / "kb"), note.load_template("general"))
        self.assertEqual(again.copied, 0)
        self.assertEqual(again.skipped_existing, 2)

    def test_creator_template_leaves_new_section_for_ai(self):
        d = tmpdir()
        (d / "old").mkdir()
        (d / "old" / "a_7650813418760359203.md").write_text(OLD_NOTE, encoding="utf-8")
        store = Store(d / "kb")
        migrate.run(d / "old", store, note.load_template("creator"))
        text = note.read(store.find("7650813418760359203"))
        self.assertEqual(note.get_status(text), note.TRANSCRIBED, "可借鉴还没写，应该留给 AI")
        self.assertIn("第一条视频卡住", text)


# ── 浏览器层 + 完整流程（本地假抖音，无界面 Chromium）─────────


class TestBrowser(unittest.TestCase):
    def test_favorites_api_incremental(self):
        with FakeDouyin(45) as fake:
            cfg = make_cfg(tmpdir(), fake.base)
            login_cookie(cfg)
            with browser.Douyin(cfg) as dy:
                self.assertTrue(dy.logged_in())
                res = dy.favorites(200)
                self.assertEqual((res.reason, res.via, len(res.items)), ("", "api", 45))
                known = {a["aweme_id"] for a in fake.favorites[20:]}
                res = dy.favorites(200, known=known)
                self.assertEqual(len(res.items), 20)
                self.assertTrue(res.stopped_at_known)

    def test_not_logged_in(self):
        with FakeDouyin(5) as fake:
            cfg = make_cfg(tmpdir(), fake.base)
            with browser.Douyin(cfg) as dy:
                self.assertFalse(dy.logged_in())
                self.assertEqual(dy.favorites(10).reason, browser.LOGIN)

    def test_server_says_not_logged_in(self):
        with FakeDouyin(5) as fake:
            cfg = make_cfg(tmpdir(), fake.base)
            with browser.Douyin(cfg) as dy:
                dy.ctx.add_cookies([{"name": "LOGIN_STATUS", "value": "1", "url": fake.base}])
                self.assertEqual(dy.favorites(10).reason, browser.LOGIN)

    def test_fallback_to_page(self):
        with FakeDouyin(45, mode="blocked") as fake:
            cfg = make_cfg(tmpdir(), fake.base)
            login_cookie(cfg)
            with browser.Douyin(cfg, log=lambda m: None) as dy:
                res = dy.favorites(45)
                self.assertEqual(res.via, "page")
                self.assertEqual(len(res.items), 45)
                known = {a["aweme_id"] for a in fake.favorites}
                res = dy.favorites(45, known=known)
                self.assertEqual((res.reason, len(res.items), res.stopped_at_known), ("", 0, True))

    def test_dead_reports_blocked(self):
        with FakeDouyin(5, mode="dead") as fake:
            cfg = make_cfg(tmpdir(), fake.base)
            login_cookie(cfg)
            with browser.Douyin(cfg, log=lambda m: None) as dy:
                res = dy.favorites(10)
                self.assertEqual(res.reason, browser.BLOCKED)
                self.assertIn("返回为空", res.detail)

    def test_captcha_is_explained(self):
        with FakeDouyin(5, mode="captcha") as fake:
            cfg = make_cfg(tmpdir(), fake.base)
            login_cookie(cfg)
            with browser.Douyin(cfg, log=lambda m: None) as dy:
                res = dy.favorites(10)
                self.assertEqual(res.reason, browser.BLOCKED)
                self.assertIn("验证码", res.detail)

    def test_server_side_expired_login(self):
        """本地还有 Cookie，但抖音说没登录 → 必须提示扫码，而不是说被限流"""
        with FakeDouyin(5, mode="expired") as fake:
            cfg = make_cfg(tmpdir(), fake.base)
            login_cookie(cfg)
            code, out, res = run_cli(["update"], cfg)
            self.assertEqual((code, res["reason"]), (cli.NEED_LOGIN, "login"), out)

    def test_other_sources(self):
        with FakeDouyin(45) as fake:
            cfg = make_cfg(tmpdir(), fake.base)
            with browser.Douyin(cfg) as dy:
                self.assertEqual(len(dy.search("AI", 25).items), 25)
                self.assertEqual(len(dy.creator("MS4wLjABAAAAtest", 40).items), 40)
                self.assertEqual(dy.detail(fake.favorites[2]["aweme_id"]).items[0]["aweme_id"],
                                 fake.favorites[2]["aweme_id"])
                comments = dy.comments(fake.favorites[2]["aweme_id"], 3)
                self.assertEqual([c["digg_count"] for c in comments], [70, 60, 50])

    def test_busy_profile(self):
        """同一个浏览器目录被另一个进程占用时，给出明确原因而不是一堆报错"""
        import subprocess
        with FakeDouyin(1) as fake:
            home = tmpdir()
            cfg = make_cfg(home, fake.base)
            code = (
                "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
                "from test_dyknow import make_cfg; from dyknow import browser; from pathlib import Path\n"
                "cfg = make_cfg(Path(%r), %r)\n"
                "try:\n    browser.Douyin(cfg).open(); print('OPENED')\n"
                "except browser.BrowserError as e:\n    print('REASON', e.reason)\n"
            ) % (str(Path(__file__).resolve().parent.parent), str(Path(__file__).resolve().parent), str(home), fake.base)
            with browser.Douyin(cfg):
                out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
            self.assertIn("REASON busy", out.stdout, out.stdout + out.stderr)


class TestFlow(unittest.TestCase):
    def test_update_full_flow_then_incremental(self):
        with FakeDouyin(25) as fake:
            home = tmpdir()
            cfg = make_cfg(home, fake.base)
            code, out, res = run_cli(["update"], cfg)
            self.assertEqual(code, cli.NEED_LOGIN, out)
            self.assertIn("dyknow login", out)

            login_cookie(cfg)
            empty_id = fake.favorites[5]["aweme_id"]
            fail_id = fake.favorites[6]["aweme_id"]
            with patched_asr(FakeAsr(empty_ids=[empty_id], fail_ids=[fail_id])):
                code, out, res = run_cli(["update"], cfg)
            self.assertEqual(code, cli.PARTIAL, "有 1 条失败，退出码应为 4：" + out)
            self.assertFalse(res["ok"])
            self.assertEqual((res["found"], res["new"], res["images"], res["transcribed"], res["no_speech"],
                              res["failed"]), (25, 25, 1, 22, 1, 1), out)
            store = Store(cfg.output_dir)
            notes = {i.id: i for i in store.notes()}
            self.assertEqual(len(notes), 25)
            self.assertTrue(all(str(i.path.parent.name) == "收藏" for i in notes.values()))
            self.assertEqual(notes[fake.favorites[3]["aweme_id"]].status, note.IMAGE)
            self.assertEqual(notes[empty_id].status, note.NO_SPEECH)
            self.assertEqual(notes[fail_id].status, note.FAILED)
            t = note.read(notes[fake.favorites[0]["aweme_id"]].path)
            self.assertEqual(note.get_status(t), note.TRANSCRIBED)
            self.assertIn("**[00:35]** 第二段内容", t)
            self.assertIn("- 👍70 「评论7」", t)
            self.assertTrue((cfg.output_dir / "索引.md").exists())
            self.assertFalse(list((cfg.cache_dir / "media").glob("*")), "临时音视频要删掉")

            # 第二次：没有新收藏，不重复转录
            fake.favorites.insert(0, make_aweme(99, fake.base))
            with patched_asr(FakeAsr()) as f2:
                code, out, res = run_cli(["update"], cfg)
            self.assertEqual((code, res["new"], f2.calls), (0, 1, 1), out)
            self.assertIn("都是已有的", out)

            # 失败的可以重试
            with patched_asr(FakeAsr()):
                code, out, res = run_cli(["transcribe", "--retry-failed"], cfg)
            self.assertEqual((code, res["transcribed"]), (0, 1), out)
            self.assertEqual(note.get_status(note.read(store.find(fail_id))), note.TRANSCRIBED)

            code, out, res = run_cli(["todo", "--limit", "3"], cfg)
            self.assertEqual(res["total"], 25)  # 23 已转录 + 1 图文 + 1 新
            self.assertEqual(len(res["files"]), 3)

    def test_interrupt_then_resume(self):
        with FakeDouyin(8) as fake:
            cfg = make_cfg(tmpdir(), fake.base, comments=0)
            login_cookie(cfg)
            with patched_asr(FakeAsr(interrupt_at=3)):
                code, out, res = run_cli(["update"], cfg)
            self.assertTrue(res["interrupted"], out)
            self.assertEqual(code, cli.PARTIAL)
            self.assertEqual(res["transcribed"], 2)
            self.assertEqual(res["pending"], 5)  # 8 条 - 1 图文 - 2 已转
            self.assertIn("dyknow transcribe", out)
            statuses = [i.status for i in Store(cfg.output_dir).notes()]
            self.assertEqual(statuses.count(note.PENDING), 5)
            with patched_asr(FakeAsr()) as f:
                code, out, res = run_cli(["transcribe"], cfg)
            self.assertEqual((code, res["transcribed"], f.calls), (0, 5, 5), out)

    def test_link_search_creator(self):
        with FakeDouyin(30) as fake:
            cfg = make_cfg(tmpdir(), fake.base)
            aid = fake.favorites[1]["aweme_id"]
            with patched_asr(FakeAsr()):
                code, out, res = run_cli(["link", f"看看这个 {fake.base}/video/{aid}?x=1 复制打开"], cfg)
                self.assertEqual((code, res["new"]), (0, 1), out)
                code, out, res = run_cli(["search", "AI", "工具", "--limit", "12"], cfg)
                self.assertEqual((code, res["found"], res["new"], res["existing"]), (0, 12, 11, 1), out)
                code, out, res = run_cli(["creator", f"{fake.base}/user/MS4wLjABAAAAtest", "--limit", "15"], cfg)
                self.assertEqual((code, res["new"]), (0, 3), out)
            folders = sorted({i.path.parent.relative_to(cfg.output_dir).as_posix() for i in Store(cfg.output_dir).notes()})
            self.assertEqual(folders, ["博主/测试博主", "搜索/AI 工具", "链接"])
            code, out, res = run_cli(["link", "没有链接的文字"], cfg)
            self.assertEqual(code, cli.ERROR)

    def test_no_transcribe_and_asr_unavailable(self):
        with FakeDouyin(4) as fake:
            cfg = make_cfg(tmpdir(), fake.base, asr="cloud")  # 没有 Key
            login_cookie(cfg)
            code, out, res = run_cli(["update"], cfg)
            self.assertEqual(code, cli.PARTIAL, out)
            self.assertEqual(res["pending"], 3)
            self.assertIn("API Key", out)
            code, out, res = run_cli(["link", "7400000000000000001", "--no-transcribe"], cfg)
            self.assertEqual(code, cli.OK, "明确要求不转录时不算失败：" + out)


class TestDownload(unittest.TestCase):
    def test_html_is_rejected_and_no_part_left(self):
        from dyknow import media
        with FakeDouyin(1) as fake:
            d = tmpdir()
            with self.assertRaises(media.DownloadError):
                media.download([fake.base + "/video/1"], d, "x")
            self.assertEqual(list(d.iterdir()), [])
            p = media.download([fake.base + "/video/1", fake.base + "/media/1.mp4"], d, "y")
            self.assertEqual(p.name, "y.mp4")


if __name__ == "__main__":
    unittest.main()


class TestAsrBackends(unittest.TestCase):
    """用假的 faster_whisper / ctranslate2 模块验证：显卡选择、失败时说清原因、段落输出"""

    def setUp(self):
        import types
        self.saved = {k: sys.modules.get(k) for k in ("faster_whisper", "ctranslate2")}
        self.loaded = []
        test = self

        class Seg:
            def __init__(self, s, e, t):
                self.start, self.end, self.text = s, e, t

        class Info:
            language = "zh"
            duration = 70.0

        class WhisperModel:
            def __init__(self, path, device, compute_type):
                test.loaded.append((path, device, compute_type))

            def detect_language(self, audio):
                return "zh", 0.98, []

            def transcribe(self, path, **kw):
                test.kw = kw
                return iter([Seg(0, 3, "大家好"), Seg(3, 5, "我用 Codex")]), Info()

        fw = types.ModuleType("faster_whisper")
        fw.WhisperModel = WhisperModel
        fw.download_model = lambda name, cache_dir=None: f"/models/{name}"
        import numpy as np
        fw.decode_audio = lambda p, sampling_rate=16000: np.zeros(16000 * 3, dtype=np.float32)
        self.cuda = 1
        ct = types.ModuleType("ctranslate2")
        ct.get_cuda_device_count = lambda: self.cuda
        ct.__version__ = "4.5.0"
        sys.modules["faster_whisper"] = fw
        sys.modules["ctranslate2"] = ct
        self.orig_check = asr.gpu_check
        os.environ.pop("HF_ENDPOINT", None)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        asr.gpu_check = self.orig_check
        os.environ.pop("HF_ENDPOINT", None)

    def test_gpu_used_when_check_passes(self):
        asr.gpu_check = lambda cfg, model, force=False: (True, "")
        b = asr.LocalWhisper(make_cfg(tmpdir()), log=lambda m: None)
        t = b.transcribe(Path("x.mp4"))
        self.assertEqual(self.loaded, [("/models/large-v3-turbo", "cuda", "float16")])
        self.assertEqual(b.describe(), "本地 large-v3-turbo（显卡）")
        self.assertEqual(t.paragraphs, [(0.0, "大家好我用 Codex")])
        self.assertEqual(self.kw["language"], "zh", "先自动识别语言")
        self.assertIn("句号", self.kw["initial_prompt"], "中文加带标点的提示词")
        self.assertTrue(self.kw["vad_filter"])
        self.assertEqual(os.environ.get("HF_ENDPOINT"), "https://hf-mirror.com")

    def test_gpu_failure_is_reported_not_hidden(self):
        asr.gpu_check = lambda cfg, model, force=False: (False, "缺少 cuDNN 运行库 → 运行：pip install …")
        logs = []
        b = asr.LocalWhisper(make_cfg(tmpdir()), log=logs.append)
        b.load()
        self.assertEqual(self.loaded, [("/models/small", "cpu", "int8")])
        self.assertIn("cuDNN", b.note)
        self.assertTrue(any("显卡没用上" in m and "cuDNN" in m for m in logs), logs)

    def test_forced_cuda_fails_loudly(self):
        asr.gpu_check = lambda cfg, model, force=False: (False, "驱动太旧")
        b = asr.LocalWhisper(make_cfg(tmpdir(), device="cuda"), log=lambda m: None)
        with self.assertRaises(asr.AsrError):
            b.load()

    def test_no_gpu(self):
        self.cuda = 0
        b = asr.LocalWhisper(make_cfg(tmpdir()), log=lambda m: None)
        b.load()
        self.assertEqual(self.loaded[0][1:], ("cpu", "int8"))
        self.assertIn("没有检测到", b.note)

    def test_make_auto(self):
        import importlib.util as iu
        orig = iu.find_spec
        iu.find_spec = lambda name, *a: object() if name == "faster_whisper" else orig(name, *a)
        try:
            self.assertIsInstance(asr.make(make_cfg(tmpdir())), asr.LocalWhisper)
            self.cuda = 0
            self.assertIsInstance(asr.make(make_cfg(tmpdir(), siliconflow_api_key="k")), asr.SiliconFlow)
            self.assertIsNone(asr.make(make_cfg(tmpdir(), asr="none")))
        finally:
            iu.find_spec = orig

    def test_cloud_chunks_with_offsets(self):
        import numpy as np
        sys.modules["faster_whisper"].decode_audio = lambda p, sampling_rate=16000: np.zeros(16000 * 400, dtype=np.float32)
        b = asr.SiliconFlow(make_cfg(tmpdir(), siliconflow_api_key="k"), log=lambda m: None)
        sent = []

        def fake_post(path):
            sent.append(path.stat().st_size)
            return "<|zh|><|NEUTRAL|>第一句。第二句。"
        b._post = fake_post
        d = tmpdir()
        t = b.transcribe(d / "a.mp4")
        self.assertEqual(len(sent), 2)  # 400 秒切成 300 + 100
        self.assertEqual([p[0] for p in t.paragraphs], [0.0, 300.0])
        self.assertEqual(list(d.iterdir()), [], "临时 wav 要删掉")


class TestRobustness(unittest.TestCase):
    def test_unreachable_site_releases_lock(self):
        cfg = make_cfg(tmpdir(), "http://127.0.0.1:9")  # 没有服务的端口
        code, out, res = run_cli(["update"], cfg)
        self.assertEqual(code, cli.PLATFORM, out)
        self.assertIn("打开页面失败", res["detail"])
        browser.ProfileLock(cfg.home / "browser.lock").acquire()  # 锁必须已释放

    def test_unexpected_error_gives_result_line(self):
        cfg = make_cfg(tmpdir())
        orig = cli.cmd_index
        cli.HANDLERS["index"] = lambda ctx, args: 1 / 0
        try:
            code, out, res = run_cli(["index"], cfg)
        finally:
            cli.HANDLERS["index"] = orig
        self.assertEqual((code, res["reason"]), (cli.ERROR, "error"))
        self.assertNotIn("Traceback", out)
        self.assertIn("Traceback", (cfg.logs_dir / "dyknow.log").read_text(encoding="utf-8"))


class TestV31(unittest.TestCase):
    """3.1：真机验证发现的问题 + 画面截图"""

    def test_dash_urls_are_excluded(self):
        v = parse_aweme(make_aweme(1, "http://x"))
        self.assertFalse(any("-dash" in u for u in v.media_urls))
        self.assertIn("low=1", v.media_urls[0])

    def test_download_skips_files_without_audio(self):
        from dyknow import media
        with FakeDouyin(1) as fake:
            d = tmpdir()
            p = media.download([fake.base + "/media/1-dash.mp4", fake.base + "/media/1.mp4"], d, "a")
            self.assertTrue(media.probe(p).has_audio)
            with self.assertRaises(media.DownloadError) as cm:
                media.download([fake.base + "/media/1-dash.mp4"], d, "b")
            self.assertIn("只有画面没有声音", str(cm.exception))
            self.assertFalse((d / "b.mp4").exists())

    def test_limit_means_checked_items(self):
        with FakeDouyin(25) as fake:
            cfg = make_cfg(tmpdir(), fake.base, comments=0)
            login_cookie(cfg)
            with patched_asr(FakeAsr()):
                code, out, res = run_cli(["update", "--limit", "5"], cfg)
                self.assertEqual((code, res["new"]), (0, 5), out)
                code, out, res = run_cli(["update", "--limit", "5"], cfg)
                self.assertEqual((code, res["new"]), (0, 0), "同样检查最新 5 条，第二次应该 0 条新的：" + out)
                self.assertIn("检查了最新 5 条，其中新的 0 条", out)
                code, out, res = run_cli(["update", "--limit", "8"], cfg)
                self.assertEqual(res["new"], 3, out)

    def test_silent_video_is_failed_not_no_speech(self):
        import fake_douyin
        with FakeDouyin(3) as fake:
            sid = fake.favorites[0]["aweme_id"]
            fake_douyin.SILENT_IDS.add(sid)
            try:
                cfg = make_cfg(tmpdir(), fake.base, comments=0)
                login_cookie(cfg)
                with patched_asr(FakeAsr()):
                    code, out, res = run_cli(["update"], cfg)
            finally:
                fake_douyin.SILENT_IDS.discard(sid)
            text = note.read(Store(cfg.output_dir).find(sid))
            self.assertEqual(note.get_status(text), note.FAILED)
            self.assertIn("只有画面没有声音", note.get_block(text, "transcript"))

    def test_frames_during_update_and_images(self):
        with FakeDouyin(5) as fake:
            cfg = make_cfg(tmpdir(), fake.base, comments=0, frames=6)
            login_cookie(cfg)
            with patched_asr(FakeAsr()):
                code, out, res = run_cli(["update"], cfg)
            self.assertEqual((code, res["frames"]), (0, 5), out)
            store = Store(cfg.output_dir)
            vid_note = store.find(fake.favorites[0]["aweme_id"])
            block = note.get_block(note.read(vid_note), "frames")
            self.assertIn("画面（", block)
            import re
            rels = re.findall(r"\]\(([^)]+)\)", block)
            self.assertGreaterEqual(len(rels), 3)
            for rel in rels:
                self.assertTrue((vid_note.parent / rel).is_file(), rel)
            text = note.read(vid_note)
            order = [text.find(f"dyknow:{b}:start") for b in ("desc", "frames", "transcript", "comments")]
            self.assertEqual(order, sorted(order))
            img_note = store.find(fake.favorites[3]["aweme_id"])
            iblock = note.get_block(note.read(img_note), "frames")
            self.assertIn("图片（3 张）", iblock)
            self.assertIn(".jpeg", "".join(fake.requests[-200:]))  # 优先下载 jpeg

    def test_frames_command_for_existing_notes(self):
        with FakeDouyin(6) as fake:
            cfg = make_cfg(tmpdir(), fake.base, comments=0)
            login_cookie(cfg)
            with patched_asr(FakeAsr()):
                run_cli(["update"], cfg)
            code, out, res = run_cli(["frames", "--limit", "2", "--count", "4"], cfg)
            self.assertEqual((code, res["frames"]), (0, 2), out)
            target = fake.favorites[5]["aweme_id"]
            code, out, res = run_cli(["frames", target], cfg)
            self.assertEqual(res["frames"], 1, out)
            self.assertIn("画面（", note.get_block(note.read(Store(cfg.output_dir).find(target)), "frames"))
            self.assertEqual(note.get_status(note.read(Store(cfg.output_dir).find(target))), note.TRANSCRIBED)

    def test_block_inserted_in_order_for_old_notes(self):
        v = Video(id="7400000000000000001", desc="x", media_urls=["u"])
        text = note.render(v, note.load_template("general"), note.PENDING)
        start, end = note._markers("frames")
        old = text.replace(start + "\n" + end + "\n\n", "")  # 模拟 3.0 版笔记（没有画面区块）
        self.assertNotIn(start, old)
        new = note.replace_block(old, "frames", "> [!note]- 画面\n> x")
        self.assertLess(new.find(start), new.find("dyknow:transcript:start"))

    def test_long_title_is_shortened(self):
        desc = ("8条视频播放超7700万次！AI网红正批量制造爆款，从虚拟DJ到AI模特，吸粉速度碾压真人。"
                "它们不知疲倦、不会塌房、成本极低 #AI网红")
        v = Video(id="7400000000000000001", desc=desc)
        self.assertEqual(v.title, "8条视频播放超7700万次！")
        text = note.render(v, note.load_template("general"), note.PENDING)
        self.assertIn("吸粉速度碾压真人", note.get_block(text, "desc"), "完整文案放在视频文案里")
        long = Video(id="1", desc="一" * 80)
        self.assertEqual(len(long.title), 51)
        self.assertTrue(long.title.endswith("…"))


class TestSearchDoesNotHang(unittest.TestCase):
    def test_streaming_response_does_not_block_page_mode(self):
        """真机上 search 卡住 4 分钟没结果：页面方式不能被不结束的流式响应卡住"""
        with FakeDouyin(30, mode="blocked") as fake:
            cfg = make_cfg(tmpdir(), fake.base)
            logs = []
            t0 = time.time()
            with browser.Douyin(cfg, log=logs.append) as dy:
                res = dy.search("AI编程", 15)
            self.assertLess(time.time() - t0, 60, logs)
            self.assertEqual((res.via, len(res.items)), ("page", 15), logs)
            self.assertTrue(any("打开页面" in m for m in logs), "要有阶段日志，卡住时能看出卡在哪")
