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


async def login_in_context(context: BrowserContext, page: Page) -> list[dict] | None:
    """在调用方的浏览器会话里扫码，成功后继续使用该会话。"""
    cookies = await context.cookies()
    if _is_logged_in(cookies):
        save_cookies(cookies)
        return cookies
    await page.goto(DOUYIN_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)
    if _is_logged_in(await context.cookies()):
        cookies = await context.cookies()
        save_cookies(cookies)
        return cookies
    # 页面已展示登录对话框时不再点击按钮。
    dialogs = page.locator('[role="dialog"]')
    visible_dialog = False
    for index in range(await dialogs.count()):
        if await dialogs.nth(index).is_visible():
            visible_dialog = True
            break
    if not visible_dialog:
        button = page.locator('//button[contains(text(),"登录")] | //span[contains(text(),"登录")]').first
        if await button.is_visible() and not _is_logged_in(await context.cookies()):
            await button.click()
    print("📱 请扫码登录；成功后将继续抓取，无需再次扫码（最多等待 5 分钟）")
    for _ in range(300):
        cookies = await context.cookies()
        if _is_logged_in(cookies):
            save_cookies(cookies)
            print("✅ 登录成功，已保存 Cookie")
            return cookies
        await asyncio.sleep(1)
    print("登录超时")
    return None


async def login(force: bool = False) -> list[dict] | None:
    """
    打开浏览器，引导用户扫码登录抖音。

    流程：
    1. 启动 Chromium（有头模式）
    2. 导航到抖音首页
    3. 等待用户扫码完成
    4. 检测登录态
    5. 保存并返回 Cookie
    """
    saved = None if force else load_cookies()
    if saved:
        print("✅ 使用已保存 Cookie；重新扫码使用 login --force")
        return saved
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
            return await login_in_context(context, page)

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
