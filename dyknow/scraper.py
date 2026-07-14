"""
收藏列表爬取 —— listcollection API + 浏览器拦截

listcollection 是抖音真正的「收藏」API，与「点赞」的 favorite API 不同。
此 API 需要可见浏览器才能正常分页（抖音反爬检测 headless），窗口移到屏幕外最小化干扰。
"""

import asyncio
import logging
from dataclasses import dataclass, field

from playwright.async_api import async_playwright

from .login import load_cookies

logger = logging.getLogger("dyknow.scraper")

COLLECTION_API = "/aweme/v1/web/aweme/listcollection/"
FAVORITE_PAGE = "https://www.douyin.com/user/self?showTab=favorite_collection"


@dataclass
class FavoriteItem:
    """单条收藏数据"""
    aweme_id: str = ""
    title: str = ""
    author: str = ""
    author_id: str = ""
    cover_url: str = ""
    video_url: str = ""
    duration: int = 0
    create_time: str = ""
    likes: int = 0
    comments: int = 0
    shares: int = 0
    plays: int = 0
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_aweme(cls, aweme: dict) -> "FavoriteItem":
        if not aweme.get("aweme_id"):
            return None

        info = cls()
        info.aweme_id = str(aweme["aweme_id"])
        info.title = aweme.get("desc", "") or "无标题"
        info.duration = aweme.get("duration", 0)

        author = aweme.get("author", {})
        info.author = author.get("nickname", "")
        info.author_id = author.get("sec_uid", "") or author.get("uid", "")

        cover = aweme.get("cover", {}) or aweme.get("video", {}).get("cover", {})
        cover_list = cover.get("url_list", [])
        info.cover_url = cover_list[-1] if cover_list else aweme.get("cover_url", "")

        video = aweme.get("video", {})
        play_addr = video.get("play_addr_h264", {}) or video.get("play_addr", {})
        url_list = play_addr.get("url_list", [])
        info.video_url = url_list[-1] if url_list else ""

        stats = aweme.get("statistics", {})
        info.likes = stats.get("digg_count", 0)
        info.comments = stats.get("comment_count", 0)
        info.shares = stats.get("share_count", 0)
        info.plays = stats.get("play_count", 0)

        info.create_time = str(aweme.get("create_time", ""))
        info.raw = aweme
        return info


async def fetch(count: int = 200) -> list[FavoriteItem]:
    """抓取收藏列表"""

    cookies = load_cookies()
    if not cookies:
        raise RuntimeError("未登录，请先运行 dyknow login")

    print(f"\n正在抓取收藏列表（目标 {count} 条）...")

    collected: list[dict] = []
    seen_ids: set[str] = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,  # 抖音反爬，必须可见浏览器
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        # ── API 拦截 ──
        async def on_response(response):
            if COLLECTION_API not in response.url:
                return
            try:
                body = await response.json()
                aweme_list = body.get("aweme_list", [])
                # 也尝试 data 嵌套结构
                if not aweme_list:
                    data = body.get("data", [])
                    if isinstance(data, list):
                        for col in data:
                            if isinstance(col, dict):
                                aweme_list.extend(col.get("aweme_list", []) or [])
                    elif isinstance(data, dict):
                        aweme_list = data.get("aweme_list", []) or []

                for aweme in aweme_list:
                    aid = aweme.get("aweme_id", "")
                    if aid and aid not in seen_ids:
                        seen_ids.add(aid)
                        collected.append(aweme)
            except Exception:
                pass

        page.on("response", on_response)

        # ── 导航到收藏页 ──
        try:
            print("   加载收藏页面...")
            await page.goto(FAVORITE_PAGE, wait_until="domcontentloaded", timeout=30000)
            # 页面加载完成后立即最小化窗口
            cdp = await browser.new_browser_cdp_session()
            try:
                await cdp.send("Browser.setWindowBounds", {
                    "windowId": 1,
                    "bounds": {"windowState": "minimized"}
                })
            except Exception:
                pass
            await asyncio.sleep(5)

            # ── 多容器滚动翻页 ──
            max_attempts = max(count // 10 + 15, 50)
            no_change = 0

            for attempt in range(max_attempts):
                prev_total = len(collected)

                # 找到所有可滚动容器并逐个滚
                await page.evaluate("""
                    () => {
                        const all = document.querySelectorAll('div, section, main');
                        for (const el of all) {
                            const s = window.getComputedStyle(el);
                            if ((s.overflowY === 'scroll' || s.overflowY === 'auto')
                                && el.scrollHeight > el.clientHeight + 50) {
                                el.scrollTop = el.scrollHeight;
                            }
                        }
                        window.scrollTo(0, document.body.scrollHeight);
                    }
                """)
                await asyncio.sleep(1.5)

                # 同时从 DOM 提取 video ID（兜底）
                dom_count = await page.evaluate("""
                    () => {
                        const ids = new Set();
                        document.querySelectorAll('a[href*="/video/"]').forEach(a => {
                            const m = a.href.match(/video\\/(\\d+)/);
                            if (m) ids.add(m[1]);
                        });
                        return ids.size;
                    }
                """)

                curr_total = len(collected)
                if (attempt + 1) % 10 == 0:
                    print(f"   [{attempt+1}] API: {curr_total} 条 | DOM: {dom_count} 个")

                if curr_total >= count:
                    break

                if curr_total == prev_total:
                    no_change += 1
                    if no_change >= 8 and dom_count > 0:
                        # API 不再增长但 DOM 还有，再等几轮
                        if no_change >= 12:
                            print(f"   连续 {no_change} 轮无新数据，停止")
                            break
                else:
                    no_change = 0

            await asyncio.sleep(2)

        finally:
            await browser.close()

    # 解析去重
    items = []
    seen = set()
    for aweme in collected:
        item = FavoriteItem.from_aweme(aweme)
        if item and item.aweme_id not in seen:
            seen.add(item.aweme_id)
            items.append(item)
            if len(items) >= count:
                break

    print(f"抓取完成: {len(items)} 条收藏")
    return items
