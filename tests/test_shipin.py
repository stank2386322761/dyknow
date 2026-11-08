"""专题页链接必须解析主视频，不能把专题编号或推荐视频当作品。"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dyknow import browser, cli, note
from dyknow.douyin import is_shipin_link, shipin_video_id_from_html, video_id_from_url
from test_dyknow import make_cfg, make_aweme, run_cli, tmpdir

PAGE_ID = "7260896160230098985"
PRIMARY = "7400000000000000002"
RELATED = "7400000000000000003"
SOURCE = "https://www.douyin.com/shipin/" + PAGE_ID


def data(vid=PRIMARY, page_id=PAGE_ID):
    return {"shipinID": page_id, "shipin": {
        "mainVideo": {"awemeId": vid, "itemId": vid,
                      "videoUrl": "https://www.douyin.com/video/" + vid},
        "relatedVideos": [{"awemeId": RELATED}]}}


def render(value):
    return '<script id="RENDER_DATA" type="application/json">' + quote(json.dumps(value)) + "</script>"


def flight(value):
    payload = "7:" + json.dumps(["$", "$L9", None, value]) + "\n"
    return "<script>self.__pace_f.push(" + json.dumps([1, payload]) + ");</script>"


class TestShipinParsing(unittest.TestCase):
    def test_detects_supported_hosts_and_query(self):
        for url in (SOURCE, SOURCE + "/?utm_source=test", SOURCE.replace("www.", ""),
                    SOURCE.replace("www.", "m.")):
            with self.subTest(url=url):
                self.assertTrue(is_shipin_link(url))

    def test_rejects_other_domains_and_routes(self):
        for url in ("https://evil.com/shipin/" + PAGE_ID,
                    "https://douyin.com.evil.com/shipin/" + PAGE_ID,
                    "https://evil.com/?u=" + SOURCE, "file://" + SOURCE,
                    SOURCE.replace("/shipin/", "/video/"), SOURCE + "/other"):
            with self.subTest(url=url):
                self.assertFalse(is_shipin_link(url))

    def test_page_number_is_not_a_video_id(self):
        self.assertEqual(video_id_from_url(SOURCE), "")

    def test_reads_percent_encoded_render_data(self):
        self.assertEqual(shipin_video_id_from_html(render({"app": data()}), SOURCE), PRIMARY)

    def test_reads_react_stream_primary_instead_of_recommendation(self):
        html = '<a href="https://www.douyin.com/video/' + RELATED + '">推荐</a>' + flight(data())
        self.assertEqual(shipin_video_id_from_html(html, SOURCE), PRIMARY)

    def test_keeps_literal_percent_encoded_text_in_stream_json(self):
        value = data()
        value["shipin"]["mainVideo"]["desc"] = "a%22b"
        self.assertEqual(shipin_video_id_from_html(flight(value), SOURCE), PRIMARY)

    def test_ignores_other_topic_data(self):
        self.assertEqual(shipin_video_id_from_html(render(data(page_id="7371050645358626835")), SOURCE), "")

    def test_does_not_guess_from_related_links(self):
        html = '<a href="https://www.douyin.com/video/' + RELATED + '">推荐</a>'
        html += render({"shipinID": PAGE_ID, "shipin": {"relatedVideos": [{"awemeId": RELATED}]}})
        self.assertEqual(shipin_video_id_from_html(html, SOURCE), "")

    def test_rejects_ambiguous_primary_videos(self):
        self.assertEqual(shipin_video_id_from_html(render([data(), data(RELATED)]), SOURCE), "")

    def test_rejects_conflicting_main_ids(self):
        value = data()
        value["shipin"]["mainVideo"]["itemId"] = RELATED
        self.assertEqual(shipin_video_id_from_html(render(value), SOURCE), "")

    def test_malformed_and_executable_script_is_not_evaluated(self):
        html = '<script>self.__pace_f.push([1, broken]);throw new Error("not executed")</script>'
        self.assertEqual(shipin_video_id_from_html(html, SOURCE), "")
        self.assertEqual(shipin_video_id_from_html(html + flight(data()), SOURCE), PRIMARY)

    def test_invalid_host_is_not_parsed(self):
        self.assertEqual(shipin_video_id_from_html(render(data()), SOURCE.replace("www.douyin.com", "evil.com")), "")


class TestShipinBrowser(unittest.TestCase):
    def client(self, html):
        client = browser.Douyin(make_cfg(tmpdir()), log=lambda *_: None)
        client.page = Mock()
        client.page.url = SOURCE
        client.page.content.return_value = html
        client._goto = Mock()
        return client

    def test_resolves_page_to_primary_video(self):
        client = self.client(flight(data()))
        self.assertEqual(client.resolve(SOURCE), "https://www.douyin.com/video/" + PRIMARY)
        client._goto.assert_called_once_with(SOURCE)

    def test_resolves_short_link_redirecting_to_topic(self):
        client = self.client(flight(data()))
        self.assertEqual(client.resolve("https://v.douyin.com/example/"), "https://www.douyin.com/video/" + PRIMARY)

    def test_missing_primary_reports_failure_without_picking_related(self):
        client = self.client(render({"shipin": {"relatedVideos": [{"awemeId": RELATED}]}}))
        with self.assertRaises(browser.BrowserError) as caught:
            client.resolve(SOURCE)
        self.assertEqual(caught.exception.reason, browser.BLOCKED)
        self.assertIn("主视频", caught.exception.detail)


class TestShipinCli(unittest.TestCase):
    def test_link_resolves_topic_before_detail_and_creates_correct_note(self):
        cfg = make_cfg(tmpdir(), comments=0)
        with patch.object(browser, "Douyin") as cls, patch("dyknow.asr.make") as asr:
            client = cls.return_value.__enter__.return_value
            client.resolve.return_value = "https://www.douyin.com/video/" + PRIMARY
            client.detail.return_value = browser.Fetch(items=[make_aweme(2, "http://fixture")], via="api")
            code, output, result = run_cli(["link", SOURCE, "--no-transcribe"], cfg)
        self.assertEqual(code, cli.OK, output)
        self.assertEqual((result["new"], result["pending"], result["transcribed"]), (1, 1, 0))
        client.resolve.assert_called_once_with(SOURCE)
        client.detail.assert_called_once_with(PRIMARY)
        asr.assert_not_called()
        path = Path(result["new_notes"][0])
        self.assertEqual(str(note.read_meta(note.read(path))["id"]), PRIMARY)

    def test_normal_video_does_not_require_topic_resolution(self):
        cfg = make_cfg(tmpdir(), comments=0)
        with patch.object(browser, "Douyin") as cls:
            client = cls.return_value.__enter__.return_value
            client.detail.return_value = browser.Fetch(items=[make_aweme(2, "http://fixture")], via="api")
            code, output, result = run_cli(["link", "https://www.douyin.com/video/" + PRIMARY, "--no-transcribe"], cfg)
        self.assertEqual(code, cli.OK, output)
        client.resolve.assert_not_called()
        client.detail.assert_called_once_with(PRIMARY)


if __name__ == "__main__":
    unittest.main()
