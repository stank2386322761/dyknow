"""
扫码登录模块 —— Playwright 打开浏览器 → 用户扫码 → 保存 Cookie
"""

import asyncio
import json
import logging
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from .config import config

logger = logging.getLogger("dyknow.login")

# 抖音首页
DOUYIN_URL = "https://www.douyin.com"
# 登录后才会出现的 Cookie name（抖音用 passport 系列 cookie）
LOGIN_COOKIE_NAMES = [
    "passport_csrf_token",
    "passport_csrf_token_default",
    "odin_tt",
    "sessionid",
    "sessionid_ss",
    "sid_guard",
    "sid_tt",
    "sid_ucp_v1",
    "ssid_ucp_v1",
    "uid_tt",
    "uid_tt_ss",
]


def _is_logged_in(cookies: list[dict]) -> bool:
    """检查 Cookie 中是否包含登录态"""
    cookie_names = {c.get("name", "") for c in cookies}
    # 至少要有 sessionid + 某个 uid 才认为已登录
    has_session = any("sessionid" in n for n in cookie_names)
    has_uid = any("uid_tt" in n for n in cookie_names)
    return has_session and has_uid


def load_cookies() -> list[dict] | None:
    """从文件加载 Cookie"""
    path = config.cookie_path
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        if _is_logged_in(cookies):
            return cookies
        else:
            logger.info("Cookie 文件存在但登录态已过期")
            return None
    except Exception:
        return None


def save_cookies(cookies: list[dict]):
    """保存 Cookie 到文件"""
    config.ensure_dirs()
    with open(config.cookie_path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    logger.info(f"Cookie 已保存: {config.cookie_path}")


async def login() -> list[dict] | None:
    """
    打开浏览器，引导用户扫码登录抖音。

    流程：
    1. 启动 Chromium（有头模式）
    2. 导航到抖音首页
    3. 等待用户扫码完成
    4. 检测登录态
    5. 保存并返回 Cookie
    """
    config.ensure_dirs()

    print("\n" + "=" * 60)
    print("🔑 DyKnow — 抖音扫码登录")
    print("=" * 60)
    print()
    print("📱 即将打开浏览器，请在浏览器中扫码登录抖音")
    print("   登录成功后程序会自动检测并保存 Cookie")
    print()

    async with async_playwright() as p:
        # 启动浏览器（扫码需要可见窗口）
        browser: Browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--window-size=500,700",
                "--window-position=100,100",
            ]
        )

        context: BrowserContext = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )

        page: Page = await context.new_page()

        try:
            # 导航到抖音首页
            print("🌐 正在打开抖音首页...")
            await page.goto(DOUYIN_URL, wait_until="domcontentloaded", timeout=30000)

            # 抖音首页默认会弹出二维码登录框，也可能需要点击"登录"按钮
            # 先等页面稳定
            await asyncio.sleep(3)

            # 尝试点击登录按钮（如果有的话），触发二维码弹窗
            try:
                login_btn = await page.wait_for_selector(
                    '//button[contains(text(),"登录")] | //span[contains(text(),"登录")]',
                    timeout=5000
                )
                if login_btn:
                    await login_btn.click()
                    print("🖱️  已点击登录按钮")
                    await asyncio.sleep(2)
            except Exception:
                pass  # 可能二维码已经弹出了

            print()
            print("📱 请在浏览器中扫码登录抖音...")
            print("   （等待中，最多 5 分钟）")
            print()

            # 轮询检测登录态（每 1 秒检查一次，最多 300 秒）
            max_wait = 300
            for i in range(max_wait):
                await asyncio.sleep(1)

                # 获取当前所有 Cookie
                cookies = await context.cookies()
                if _is_logged_in(cookies):
                    print("\n✅ 登录成功！")
                    save_cookies(cookies)
                    return cookies

                # 每 10 秒输出一个点，让用户知道还在等
                if (i + 1) % 10 == 0:
                    dots = (i + 1) // 10
                    print(f"   ⏳ 等待扫码中... {'.' * dots} ({i + 1}s)")

            print("\n⚠️ 登录超时（5 分钟），请重试")
            return None

        except Exception as e:
            print(f"\n❌ 登录过程出错: {e}")
            raise

        finally:
            await browser.close()


async def check_login() -> bool:
    """
    静默检查 Cookie 是否仍然有效。
    用保存的 Cookie 访问抖音个人设置页，看是否会重定向到登录页。
    """
    cookies = load_cookies()
    if not cookies:
        return False

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(cookies)

        page = await context.new_page()

        try:
            # 访问需要登录的页面
            resp = await page.goto(
                "https://www.douyin.com/user/self",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await page.wait_for_timeout(2000)

            current_url = page.url
            # 如果被重定向到登录页或首页，说明 Cookie 过期
            if "login" in current_url.lower() or current_url == DOUYIN_URL + "/":
                logger.info("Cookie 已过期，需要重新登录")
                await browser.close()
                return False

            # 再检查 Cookie
            new_cookies = await context.cookies()
            await browser.close()
            return _is_logged_in(new_cookies)

        except Exception:
            await browser.close()
            return False


def ensure_login() -> list[dict]:
    """
    同步包装：确保有有效的登录 Cookie。
    如果没有或过期，启动交互式登录流程。
    """
    # 先尝试加载已有 Cookie
    cookies = load_cookies()
    if cookies:
        print("✅ 使用已保存的 Cookie")
        return cookies

    # 没有或过期 → 启动登录
    print("🔑 需要登录抖音...")
    return asyncio.run(login())
