"""找选题：热榜 + 跨平台找对标（本地假网站）"""

from __future__ import annotations

import sys
import time
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dyknow import cli, note, sites  # noqa: E402
from dyknow.sites import xhs, zhihu  # noqa: E402
from dyknow.store import Store  # noqa: E402
from fake_douyin import FakeDouyin  # noqa: E402
from fake_sites import FakeSites  # noqa: E402
from test_dyknow import FakeAsr, make_cfg, patched_asr, run_cli, tmpdir  # noqa: E402


def research_cfg(fd: FakeDouyin, fs: FakeSites, login=("xhs", "zhihu"), **kw):
    cfg = make_cfg(tmpdir(), fd.base, **kw)
    cfg.site_urls = fs.urls()
    from dyknow import browser
    cookies = []
    if "xhs" in login:
        cookies.append({"name": "web_session", "value": "loggedin", "url": fs.base, "expires": int(time.time()) + 86400})
    if "zhihu" in login:
        cookies.append({"name": "z_c0", "value": "token", "url": fs.base, "expires": int(time.time()) + 86400})
    if cookies:
        with browser.Douyin(cfg) as b:
            b.ctx.add_cookies(cookies)
    return cfg


class TestHelpers(unittest.TestCase):
    def test_counts_and_times(self):
        self.assertEqual(sites.parse_count("1.2万"), 12000)
        self.assertEqual(sites.parse_count("10w+"), 100000)
        self.assertEqual(sites.parse_count(" 3,456 "), 3456)
        self.assertEqual(sites.parse_count(None), 0)
        today = date(2026, 10, 14)
        self.assertEqual(sites.parse_cn_time("3天前", today), "2026-10-11")
        self.assertEqual(sites.parse_cn_time("5小时前", today), "2026-10-14")
        self.assertEqual(sites.parse_cn_time("昨天 12:30", today), "2026-10-13")
        self.assertEqual(sites.parse_cn_time("10-01", today), "2026-10-01")
        self.assertEqual(sites.parse_cn_time("12-25", today), "2025-12-25")
        self.assertEqual(sites.parse_cn_time("2023-01-05", today), "2023-01-05")
        self.assertEqual(sites.parse_cn_time("编辑于 2024年3月2日", today), "2024-03-02")
        self.assertEqual(sites.within_days("2026-10-01", 30, today), True)
        self.assertEqual(sites.within_days("2026-01-01", 30, today), False)
        self.assertIsNone(sites.within_days("", 30, today))

    def test_platform_names(self):
        self.assertEqual(sites.platforms("小红书，知乎 B站,dy", []), ["xhs", "zhihu", "bili", "douyin"])
        self.assertEqual(sites.platforms("", ["zhihu"]), ["zhihu"])

    def test_html_to_text(self):
        self.assertEqual(sites.html_to_text("<p>一</p><p>二 &amp; <b>三</b></p><ul><li>a</li></ul>"), "一\n二 & 三\n- a")

    def test_prefixed_ids_in_store(self):
        d = tmpdir() / "kb"
        (d / "对标" / "x").mkdir(parents=True)
        for name in ("2026-10-14_标题_xhs-0000000000000000064f0001.md", "2026-10-14_标题_zh-a1001.md",
                     "2026-10-14_标题_bili-BV1xx00001.md", "对标报告_2026-10-14.md"):
            (d / "对标" / "x" / name).write_text("x", encoding="utf-8")
        ids = Store(d).scan()
        self.assertEqual(sorted(ids), ["bili-BV1xx00001", "xhs-0000000000000000064f0001", "zh-a1001"])

    def test_zhihu_and_xhs_parsers(self):
        it = zhihu.parse_object({"type": "answer", "id": "9", "question": {"id": "8", "name": "<em>问</em>题"},
                                 "voteup_count": 5, "created_time": 1760000000})
        self.assertEqual((it.id, it.title, it.url), ("zh-a9", "问题", "https://www.zhihu.com/question/8/answer/9"))
        self.assertIsNone(zhihu.parse_object({"type": "question", "id": "1"}))
        e = {"id": "abc123", "xsec_token": "t=1", "note_card": {"display_title": "x", "interact_info": {"liked_count": "2.5万"}}}
        it = xhs.parse_search_entry(e)
        self.assertEqual((it.id, it.likes), ("xhs-abc123", 25000))
        self.assertIn("xsec_token=t%3D1", it.url)


class TestHot(unittest.TestCase):
    def test_hot_all_platforms(self):
        with FakeDouyin(3) as fd, FakeSites() as fs:
            cfg = research_cfg(fd, fs, niche="AI 工具")
            code, out, res = run_cli(["hot", "--limit", "10"], cfg)
            self.assertEqual(code, cli.OK, out)
            self.assertEqual(res["platforms"], {"抖音": 10, "知乎": 10, "微博": 10, "B站": 10, "小红书": 8})
            report = Path(res["report"])
            text = report.read_text(encoding="utf-8")
            self.assertEqual(report.parent.name, "热榜")
            self.assertIn("## 和我有关的热点", text)
            self.assertIn("AI 工具", text)
            self.assertIn("1. [抖音热点0]", text)
            self.assertIn("知乎热榜问题0", text)
            self.assertNotIn("广告位", text)
            self.assertIn("B站热门0", text)
            self.assertIn("热搜0", text)


class TestFind(unittest.TestCase):
    def test_find_ranks_filters_and_reports(self):
        with FakeDouyin(12) as fd, FakeSites() as fs:
            cfg = research_cfg(fd, fs)
            code, out, res = run_cli(["find", "AI编程", "--days", "30", "--limit", "5"], cfg)
            self.assertEqual(code, cli.OK, out)
            self.assertEqual(res["platforms"], {"小红书": 5, "知乎": 5, "B站": 5, "抖音": 5}, out)
            text = Path(res["report"]).read_text(encoding="utf-8")
            self.assertIn("## 选题建议", text)
            self.assertIn("会有人主动搜吗", text)
            self.assertNotIn("2023-01-05", text, "30 天以前的要去掉")
            # 小红书按点赞排序：第一条应该是点赞最多的
            xhs_rows = [ln for ln in text.splitlines() if "小红书笔记" in ln and ln.startswith("| ")]
            likes = [sites.parse_count(r.split("|")[5]) for r in xhs_rows]
            self.assertEqual(likes, sorted(likes, reverse=True), xhs_rows)
            self.assertEqual(likes[0], 120000)
            self.assertIn("AI编程该怎么入门", text)  # 知乎标题去掉了 <em>
            self.assertIn("AI编程教程", text)
            self.assertTrue(any("sort_type=1" in q for q in fd.queries), "抖音要按点赞搜索")
            self.assertTrue(Path(res["raw_dir"]).is_dir(), "原始数据要存一份，方便排查")

    def test_find_save_creates_full_notes(self):
        with FakeDouyin(6) as fd, FakeSites() as fs:
            cfg = research_cfg(fd, fs, comments=2)
            with patched_asr(FakeAsr()) as asr_fake:
                code, out, res = run_cli(["find", "AI编程", "--limit", "3", "--save", "2"], cfg)
            self.assertEqual(code, cli.OK, out)
            self.assertEqual(res["saved"], 8, out)
            store = Store(cfg.output_dir)
            infos = {i.id: i for i in store.notes()}
            folders = {i.path.parent.relative_to(cfg.output_dir).as_posix() for i in infos.values()}
            self.assertEqual(folders, {"对标/AI编程"})
            # 小红书图文：正文 + 图片
            x = next(i for k, i in infos.items() if k.startswith("xhs-") and i.status == note.READY)
            t = note.read(x.path)
            self.assertIn("第一步装好工具", note.get_block(t, "desc"))
            self.assertIn("图片（3 张）", note.get_block(t, "frames"))
            self.assertEqual(note.read_meta(t)["platform"], "小红书")
            # 小红书视频、B站视频：转录
            self.assertTrue(any(k.startswith("xhs-") and i.status == note.TRANSCRIBED for k, i in infos.items()))
            bili_notes = [i for k, i in infos.items() if k.startswith("bili-")]
            self.assertEqual({i.status for i in bili_notes}, {note.TRANSCRIBED})
            self.assertIn("简介", note.get_block(note.read(bili_notes[0].path), "desc"))
            # 知乎：回答全文
            z = next(i for k, i in infos.items() if k.startswith("zh-"))
            self.assertIn("先选工具", note.get_block(note.read(z.path), "desc"))
            self.assertEqual(z.status, note.READY)
            # 抖音：原来的流程（转录 + 评论）
            d = next(i for k, i in infos.items() if k.isdigit())
            self.assertEqual(d.status, note.TRANSCRIBED)
            # 报告里能点到本地笔记
            report = Path(res["report"]).read_text(encoding="utf-8")
            self.assertEqual(report.count("📝]]"), 8)
            # 都进了待整理清单
            code, out, res = run_cli(["todo", "--limit", "50"], cfg)
            self.assertEqual(res["total"], 8)
            # 再跑一次不会重复建笔记
            with patched_asr(FakeAsr()):
                code, out, res = run_cli(["find", "AI编程", "--limit", "3", "--save", "2"], cfg)
            self.assertEqual(res["saved"], 0, out)

    def test_not_logged_in_platforms_are_reported(self):
        with FakeDouyin(4) as fd, FakeSites() as fs:
            cfg = research_cfg(fd, fs, login=())
            code, out, res = run_cli(["find", "AI编程", "--limit", "3"], cfg)
            self.assertEqual(code, cli.PARTIAL, out)
            self.assertEqual(set(res["errors"]), {"小红书", "知乎"})
            self.assertIn("dyknow login --site xhs", out)
            self.assertIn("dyknow login --site zhihu", out)
            self.assertEqual(res["platforms"], {"B站": 3, "抖音": 3})

    def test_changed_structure_is_reported_with_raw_data(self):
        with FakeDouyin(2) as fd, FakeSites(xhs_broken=True) as fs:
            cfg = research_cfg(fd, fs)
            code, out, res = run_cli(["find", "AI", "--platforms", "xhs"], cfg)
            self.assertEqual(code, cli.PLATFORM, out)
            self.assertIn("解析不出来", res["errors"]["小红书"])
            raw = Path(res["raw_dir"])
            self.assertTrue(list(raw.glob("*search_notes*.json")), list(raw.iterdir()) if raw.exists() else "no raw")
            self.assertIn("发给开发者", out)

    def test_login_site_detects_existing_login(self):
        with FakeDouyin(1) as fd, FakeSites() as fs:
            cfg = research_cfg(fd, fs, login=("zhihu",))
            from dyknow import browser
            from dyknow.sites import zhihu as z
            orig, orig_b = z.LOGIN_URL, browser.Browser

            class Headless(orig_b):  # 测试环境没有屏幕，登录窗口改成无界面
                def __init__(self, c, headless=None, **kw):
                    super().__init__(c, headless=True, **kw)

            z.LOGIN_URL, browser.Browser = fs.base + "/signin", Headless
            try:
                code, out, res = run_cli(["login", "--site", "知乎"], cfg)
            finally:
                z.LOGIN_URL, browser.Browser = orig, orig_b
            self.assertEqual((code, res.get("site")), (cli.OK, "zhihu"), out)


if __name__ == "__main__":
    unittest.main()
