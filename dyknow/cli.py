"""命令行入口。

每条命令最后都打印两行固定格式，方便任何 AI 读取：
    下一步：<该做什么>
    RESULT: {"ok": ..., "code": ..., ...}
退出码：0 全部成功 / 1 出错 / 2 需要扫码登录 / 3 抖音或浏览器问题（看 RESULT 里的 reason）
       4 命令跑完了，但有条目失败或被中断（看 RESULT 里的 failed / pending）
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, asr, browser, index, migrate, note
from .config import DEFAULTS, Config, load
from .douyin import (Video, find_links, is_short_link, parse_aweme, sec_uid_from_url,
                     video_id_from_url)
from .pipeline import Runner, Stats
from .store import FOLDER_CREATOR, FOLDER_FAVORITES, FOLDER_LINKS, FOLDER_SEARCH, Store

OK, ERROR, NEED_LOGIN, PLATFORM, PARTIAL = 0, 1, 2, 3, 4

NEXT_STEP = {
    browser.LOGIN: "运行 dyknow login 扫码登录（只需一次），然后重新运行刚才的命令",
    browser.BLOCKED: "等 10-30 分钟再试，不要连续重试；还不行就运行 dyknow doctor，把输出发给开发者",
    browser.BUSY: "关掉其他正在运行的 dyknow 和它打开的浏览器窗口，再重试",
    browser.NO_BROWSER: "安装 Chrome 浏览器，或运行：python -m playwright install chromium",
    browser.EMPTY: "确认传入的链接对不对（博主主页链接或分享链接）",
}


class Ctx:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        cfg.logs_dir.mkdir(parents=True, exist_ok=True)
        self._log = open(cfg.logs_dir / "dyknow.log", "a", encoding="utf-8")
        log_args = sys.argv[1:]
        config_args = [arg for arg in log_args if arg != "--"]
        if config_args[:2] == ["config", "siliconflow_api_key"] and len(config_args) > 2:
            log_args = config_args[:2] + ["[hidden]"]
        self._log.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(log_args)}\n")

    def log(self, msg: str = "") -> None:
        print(msg, flush=True)
        self._log.write(msg + "\n")
        self._log.flush()

    def finish(self, command: str, code: int, next_step: str = "", **data) -> int:
        payload = {"ok": code == OK, "code": code, "command": command, **data}
        if next_step:
            self.log(f"\n下一步：{next_step}")
        self.log("RESULT: " + json.dumps(payload, ensure_ascii=False))
        self._log.close()
        return code


def _platform_fail(ctx: Ctx, command: str, reason: str, detail: str = "") -> int:
    text = browser.REASON_TEXT.get(reason, reason)
    ctx.log(f"\n❌ {text}" + (f"（{detail}）" if detail else ""))
    code = NEED_LOGIN if reason == browser.LOGIN else PLATFORM
    return ctx.finish(command, code, NEXT_STEP.get(reason, ""), reason=reason, detail=detail)


def _report(ctx: Ctx, stats: Stats, root: Path) -> None:
    ctx.log("")
    ctx.log(f"抓到 {stats.found} 条：新建笔记 {stats.new} 条，之前已有 {stats.existing} 条")
    if stats.engine:
        ctx.log(f"转录（{stats.engine}）：成功 {stats.transcribed}，无语音 {stats.no_speech}，"
                f"失败 {stats.failed}，未转录 {stats.pending}，图文 {stats.images}，用时 {stats.seconds:.0f} 秒")
    elif stats.pending:
        ctx.log(f"未转录 {stats.pending} 条" + (f"：{stats.engine_note}" if stats.engine_note else ""))
    if stats.frames:
        ctx.log(f"画面截图：{stats.frames} 条笔记已加上截图")
    if stats.engine_note and stats.engine:
        ctx.log(f"⚠️ 显卡没用上：{stats.engine_note}")
    for f in stats.failures[:5]:
        ctx.log(f"   失败：{f}")
    ctx.log(f"笔记目录：{root}")


def _next_after(stats: Stats) -> str:
    if stats.pending:
        return "运行 dyknow transcribe 继续转录没做完的"
    if stats.failed:
        return "可以稍后运行 dyknow transcribe --retry-failed 重试失败的；其余笔记运行 dyknow todo 交给 AI 整理"
    if stats.new:
        return "运行 dyknow todo 列出待整理的笔记，交给 AI 按笔记里的说明填写"
    return ""


def _stats_payload(stats: Stats, root: Path) -> dict:
    return {
        "found": stats.found, "new": stats.new, "existing": stats.existing,
        "transcribed": stats.transcribed, "no_speech": stats.no_speech, "images": stats.images,
        "failed": stats.failed, "pending": stats.pending, "interrupted": stats.interrupted,
        "frames": stats.frames,
        "engine": stats.engine, "gpu_note": stats.engine_note, "notes_dir": str(root),
        "new_notes": stats.new_paths[:50],
    }


def _parse_all(items: list[dict]) -> list[Video]:
    out = []
    for aw in items:
        v = parse_aweme(aw)
        if v:
            out.append(v)
    return out


def _collect_and_run(ctx: Ctx, command: str, fetcher, folder_for, no_transcribe: bool,
                     day_from_published: bool = False, need_login: bool = False) -> int:
    """通用流程：开浏览器 → 抓 → 建笔记（含评论）→ 关浏览器 → 转录 → 更新索引"""
    cfg = ctx.cfg
    runner = Runner(cfg, ctx.log)
    stats = Stats()
    try:
        with browser.Douyin(cfg, log=ctx.log) as dy:
            if need_login and not dy.logged_in():
                return _platform_fail(ctx, command, browser.LOGIN)
            dy.minimize()
            res, label = fetcher(dy, runner.store)
            if res.reason:
                return _platform_fail(ctx, command, res.reason, res.detail)
            videos = _parse_all(res.items)
            way = "接口" if res.via == "api" else "页面"
            if res.examined:
                ctx.log(f"{label}：检查了最新 {res.examined} 条，其中新的 {len(videos)} 条（{way}方式）"
                        + ("，再往后都是已有的" if res.stopped_at_known and res.examined < getattr(fetcher, "limit", 10**9) else ""))
            else:
                ctx.log(f"{label}：拿到 {len(videos)} 条（{way}方式）")
            folder = folder_for(videos)
            jobs = runner.add(videos, folder, stats, dy=dy, day_from_published=day_from_published)
    except browser.BrowserError as e:
        return _platform_fail(ctx, command, e.reason, e.detail)
    if no_transcribe:
        stats.pending += len(jobs)
    else:
        runner.transcribe(jobs, stats)
    index.rebuild(runner.store)
    _report(ctx, stats, cfg.output_dir)
    code = (PARTIAL if stats.failed else OK) if no_transcribe else _code_for(stats)
    return ctx.finish(command, code, _next_after(stats), **_stats_payload(stats, cfg.output_dir))


def _code_for(stats: Stats) -> int:
    """命令跑完但有失败 / 中断 / 没转录的，用 4 表示，避免被当成「全部成功」"""
    return PARTIAL if (stats.failed or stats.interrupted or stats.pending) else OK


# ── 命令 ──────────────────────────────────────


def cmd_login(ctx: Ctx, args) -> int:
    try:
        with browser.Douyin(ctx.cfg, headless=False, log=ctx.log) as dy:
            force = args.force
            if dy.logged_in() and not force:
                try:
                    dy.api(browser.API_COLLECTION, method="POST", body={"count": "1", "cursor": "0"})
                    ctx.log("✅ 已经是登录状态，不需要再扫码。")
                    return ctx.finish("login", OK, "直接运行 dyknow update 同步收藏")
                except browser.ApiFail as e:
                    if e.reason == browser.LOGIN:
                        ctx.log("登录已在抖音那边过期，需要重新扫码。")
                        force = True
                    else:
                        ctx.log("✅ 本机保存着登录状态（暂时没法向抖音确认，不影响使用）。")
                        return ctx.finish("login", OK, "直接运行 dyknow update 同步收藏")
            if force:
                dy.ctx.clear_cookies()
                dy.page.reload()
                dy.page.wait_for_timeout(2000)
            if dy.page_state() == "captcha":
                ctx.log("🧩 页面上有验证码，请先在浏览器里手动完成验证。")
            dy.open_login_dialog()
            ctx.log("📱 浏览器已打开：如果没有自动弹出登录框，点页面右上角「登录」，用抖音 App 扫码（最多等 5 分钟）…")
            if not dy.wait_for_login(300):
                return ctx.finish("login", NEED_LOGIN, "重新运行 dyknow login，并在 5 分钟内扫码", reason="timeout")
            ctx.log("✅ 登录成功。登录状态保存在本机，以后不用再扫码。")
            return ctx.finish("login", OK, "运行 dyknow update 同步收藏")
    except browser.BrowserError as e:
        return _platform_fail(ctx, "login", e.reason, e.detail)


def cmd_update(ctx: Ctx, args) -> int:
    limit = args.limit or int(ctx.cfg.get("update_limit"))

    def fetch(dy, store):
        known = set(store.scan())
        return dy.favorites(limit, known=known), "收藏"
    fetch.limit = limit

    ctx.log(f"同步收藏（检查最新 {limit} 条）…")
    return _collect_and_run(ctx, "update", fetch, lambda vs: Store(ctx.cfg.output_dir).folder(FOLDER_FAVORITES),
                            args.no_transcribe, need_login=True)


def cmd_link(ctx: Ctx, args) -> int:
    links = find_links(" ".join(args.text))
    if not links:
        ctx.log("❌ 没有在文字里找到抖音链接或视频 ID")
        return ctx.finish("link", ERROR, "把抖音 App 里「分享 → 复制链接」得到的整段文字原样传进来", reason="no_link")

    def fetch(dy, store):
        items, problems = [], []
        for link in links:
            url = link
            if link.isdigit():
                vid = link
            else:
                if is_short_link(link):
                    url = dy.resolve(link)
                vid = video_id_from_url(url)
            if not vid:
                problems.append(f"{link} 不是作品链接" + ("（这是博主主页，请用 dyknow creator）" if sec_uid_from_url(url) else ""))
                continue
            r = dy.detail(vid)
            if r.reason:
                if r.reason == browser.LOGIN:
                    return r, "链接"
                problems.append(f"{vid}：{browser.REASON_TEXT.get(r.reason, r.reason)}")
                continue
            items += r.items
        for p in problems:
            ctx.log(f"   ⚠️ {p}")
        res = browser.Fetch(items=items, via="api")
        if not items:
            res.reason, res.detail = browser.BLOCKED, "；".join(problems)[:200]
        return res, "链接"

    return _collect_and_run(ctx, "link", fetch, lambda vs: Store(ctx.cfg.output_dir).folder(FOLDER_LINKS),
                            args.no_transcribe)


def cmd_search(ctx: Ctx, args) -> int:
    kw = " ".join(args.keyword).strip()
    ctx.log(f"搜索「{kw}」（最多 {args.limit} 条）…")
    return _collect_and_run(ctx, "search", lambda dy, store: (dy.search(kw, args.limit), f"搜索「{kw}」"),
                            lambda vs: Store(ctx.cfg.output_dir).folder(FOLDER_SEARCH, kw),
                            args.no_transcribe, day_from_published=True)


def cmd_creator(ctx: Ctx, args) -> int:
    raw = args.url.strip()

    def fetch(dy, store):
        url = raw
        if is_short_link(raw):
            url = dy.resolve(raw)
        sec = sec_uid_from_url(url) or (raw if raw.startswith("MS4w") else "")
        if not sec:
            return browser.Fetch(reason=browser.EMPTY, detail="不是博主主页链接"), "博主"
        return dy.creator(sec, args.limit), "博主作品"

    def folder(vs):
        name = vs[0].author if vs else "未知博主"
        return Store(ctx.cfg.output_dir).folder(FOLDER_CREATOR, name)

    ctx.log(f"抓取博主作品（最多 {args.limit} 条）…")
    return _collect_and_run(ctx, "creator", fetch, folder, args.no_transcribe, day_from_published=True)


def cmd_transcribe(ctx: Ctx, args) -> int:
    cfg = ctx.cfg
    runner = Runner(cfg, ctx.log)
    wanted = {note.PENDING} | ({note.FAILED} if args.retry_failed else set())
    infos = [i for i in runner.store.notes() if i.status in wanted]
    if args.limit:
        infos = infos[:args.limit]
    stats = Stats()
    if not infos:
        ctx.log("没有需要转录的笔记。")
        return ctx.finish("transcribe", OK, "", **_stats_payload(stats, cfg.output_dir))
    ctx.log(f"有 {len(infos)} 条需要转录，先获取最新的下载地址…")
    jobs = []
    try:
        with browser.Douyin(cfg, log=ctx.log) as dy:
            dy.minimize()
            for i in infos:
                r = dy.detail(i.id)
                if r.reason == browser.LOGIN:
                    return _platform_fail(ctx, "transcribe", r.reason, r.detail)
                v = parse_aweme(r.items[0]) if r.items else None
                if not v:
                    stats.failed += 1
                    stats.failures.append(f"{i.id}：拿不到作品信息（可能已删除）")
                    runner._write_failed(i.path, "拿不到作品信息（作品可能已删除或设为私密）")
                    continue
                if v.is_image:
                    text = note.read(i.path)
                    text = note.replace_block(text, "transcript", "> [!note]- 转写\n> 图文作品，没有语音。")
                    note.write_atomic(i.path, note.set_status(text, note.IMAGE))
                    stats.images += 1
                    continue
                jobs.append(runner_job(v, i.path))
    except browser.BrowserError as e:
        return _platform_fail(ctx, "transcribe", e.reason, e.detail)
    stats.found = len(infos)
    runner.transcribe(jobs, stats)
    index.rebuild(runner.store)
    _report(ctx, stats, cfg.output_dir)
    return ctx.finish("transcribe", _code_for(stats), _next_after(stats), **_stats_payload(stats, cfg.output_dir))


def cmd_frames(ctx: Ctx, args) -> int:
    """给已有笔记补上画面截图（给能看图的 AI 用）"""
    cfg = ctx.cfg
    runner = Runner(cfg, ctx.log)
    if args.count:
        runner.frames_n = args.count
    if runner.frames_n <= 0:
        runner.frames_n = 8
    ids = set(find_links(" ".join(args.ids))) if args.ids else set()
    infos = [i for i in runner.store.notes()
             if (i.id in ids if ids else not note.get_block(note.read(i.path), "frames"))]
    if args.limit and not ids:
        infos = infos[:args.limit]
    stats = Stats()
    if not infos:
        ctx.log("没有需要截图的笔记。")
        return ctx.finish("frames", OK, "", **_stats_payload(stats, cfg.output_dir))
    ctx.log(f"给 {len(infos)} 条笔记截取画面（每条最多 {runner.frames_n} 张），先获取下载地址…")
    jobs = []
    try:
        with browser.Douyin(cfg, log=ctx.log) as dy:
            dy.minimize()
            for i in infos:
                r = dy.detail(i.id)
                if r.reason == browser.LOGIN:
                    return _platform_fail(ctx, "frames", r.reason, r.detail)
                v = parse_aweme(r.items[0]) if r.items else None
                if not v:
                    stats.failed += 1
                    stats.failures.append(f"{i.id}：拿不到作品信息（可能已删除）")
                    continue
                jobs.append(runner_job(v, i.path))
    except browser.BrowserError as e:
        return _platform_fail(ctx, "frames", e.reason, e.detail)
    stats.found = len(infos)
    runner.frames_only(jobs, stats)
    _report(ctx, stats, cfg.output_dir)
    return ctx.finish("frames", PARTIAL if stats.failed else OK, "", **_stats_payload(stats, cfg.output_dir))


def runner_job(v: Video, path: Path):
    from .pipeline import Job
    return Job(v, path)


def cmd_todo(ctx: Ctx, args) -> int:
    store = Store(ctx.cfg.output_dir)
    infos = [i for i in store.notes() if i.status in note.TODO_STATUSES]
    total = len(infos)
    infos = infos[:args.limit] if args.limit else infos
    if not total:
        ctx.log("没有等待整理的笔记。")
        return ctx.finish("todo", OK, "", total=0, files=[])
    ctx.log(f"等待 AI 整理：{total} 条" + (f"（下面列出前 {len(infos)} 条）" if len(infos) < total else ""))
    for i in infos:
        ctx.log(f"- {i.path}")
    ctx.log("\n整理方法（每个文件）：")
    ctx.log("1. 读文件里的「转写」和「视频文案」")
    ctx.log("2. 在每个 <!-- AI：… --> 说明的下一行写内容，说明注释保留不动；只改这些位置，别的地方不要动")
    ctx.log("3. 写完把开头的 status 改成 \"已整理\"")
    ctx.log("全部写完后运行 dyknow index 更新索引。")
    return ctx.finish("todo", OK, "按上面的方法逐个整理文件", total=total, files=[str(i.path) for i in infos])


def cmd_index(ctx: Ctx, args) -> int:
    store = Store(ctx.cfg.output_dir)
    if not store.root.is_dir():
        ctx.log(f"笔记目录还不存在：{store.root}")
        return ctx.finish("index", OK, "先运行 dyknow update")
    counts = index.rebuild(store)
    ctx.log(f"索引已更新：{store.root / '索引.md'}（共 {sum(counts.values())} 条）")
    return ctx.finish("index", OK, "", counts=dict(counts))


def _config_display(key: str, value):
    return "[hidden]" if key == "siliconflow_api_key" and value else value


def cmd_config(ctx: Ctx, args) -> int:
    cfg = ctx.cfg
    if args.key:
        if args.key not in DEFAULTS:
            ctx.log(f"没有这个配置项：{args.key}。可用：{', '.join(DEFAULTS)}")
            return ctx.finish("config", ERROR, reason="unknown_key")
        if args.value is None:
            ctx.log(f"{args.key} = {_config_display(args.key, cfg.get(args.key))}（{cfg.source_of(args.key)}）")
            return ctx.finish("config", OK)
        if args.key == "template":
            note.load_template(args.value)  # 先检查模板存在
        value = _config_display(args.key, cfg.set(args.key, args.value))
        ctx.log(f"已保存：{args.key} = {value}")
        return ctx.finish("config", OK, key=args.key, value=value)
    ctx.log(f"配置文件：{cfg.path}\n")
    for key, (default, desc) in DEFAULTS.items():
        val = cfg.get(key)
        val = _config_display(key, val)
        ctx.log(f"{key} = {val}    # {desc}（{cfg.source_of(key)}）")
    ctx.log("\n修改：dyknow config <配置项> <值>，例如 dyknow config output_dir \"D:/知识库/抖音\"")
    return ctx.finish("config", OK)


def cmd_migrate(ctx: Ctx, args) -> int:
    old = Path(args.old_dir).expanduser()
    if not old.is_dir():
        ctx.log(f"找不到旧目录：{old}")
        return ctx.finish("migrate", ERROR, reason="no_dir")
    store = Store(ctx.cfg.output_dir)
    if old.resolve() == store.root.resolve():
        ctx.log("旧目录和新的笔记目录不能是同一个。先用 dyknow config output_dir 设一个新目录。")
        return ctx.finish("migrate", ERROR, reason="same_dir")
    template = note.load_template(str(ctx.cfg.get("template")))
    res = migrate.run(old, store, template, dry_run=args.dry_run)
    verb = "将复制" if args.dry_run else "已复制"
    ctx.log(f"{verb} {res.copied} 条（其中已整理 {res.organized} 条），新目录已有而跳过 {res.skipped_existing} 条，"
            f"不是视频笔记跳过 {res.skipped_other} 个文件。旧文件没有任何改动。")
    for e in res.errors[:10]:
        ctx.log(f"   出错：{e}")
    if not args.dry_run:
        index.rebuild(store)
    nxt = "去掉 --dry-run 正式复制" if args.dry_run else "运行 dyknow transcribe 补转录旧版没转完的；dyknow todo 查看待整理的"
    return ctx.finish("migrate", OK, nxt, copied=res.copied, organized=res.organized,
                      skipped_existing=res.skipped_existing, errors=len(res.errors))


def cmd_doctor(ctx: Ctx, args) -> int:
    cfg = ctx.cfg
    problems: list[str] = []

    def check(ok: bool, good: str, bad: str, fix: str = "") -> None:
        if ok:
            ctx.log(f"✅ {good}")
        else:
            ctx.log(f"❌ {bad}" + (f"\n   → {fix}" if fix else ""))
            problems.append(bad)

    ctx.log(f"DyKnow {__version__} · Python {sys.version.split()[0]} · 数据目录 {cfg.home}\n")
    check(sys.version_info >= (3, 10), "Python 版本可以", "Python 太旧", "安装 Python 3.10 或更新版本")

    out = cfg.output_dir
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe = out / ".dyknow_write_test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        check(True, f"笔记目录可写：{out}", "")
    except OSError as e:
        check(False, "", f"笔记目录不能写：{out}（{e}）", "dyknow config output_dir <新目录>")

    from . import media
    check(bool(media.ffmpeg_exe()), "ffmpeg 可用（检查音轨、截取画面用）", "找不到 ffmpeg",
          "pip install imageio-ffmpeg")

    # 转录
    mode = str(cfg.get("asr"))
    has_fw = asr.importlib.util.find_spec("faster_whisper") is not None
    if mode in ("auto", "local"):
        check(has_fw, "已安装 faster-whisper", "没有安装 faster-whisper", "pip install faster-whisper")
    smi = shutil.which("nvidia-smi")
    gpu_name = ""
    if smi:
        try:
            gpu_name = subprocess.run([smi, "--query-gpu=name,driver_version", "--format=csv,noheader"],
                                      capture_output=True, text=True, timeout=15).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    if gpu_name:
        ctx.log(f"ℹ️ 显卡：{gpu_name}")
        if has_fw and mode in ("auto", "local") and str(cfg.get("device")) != "cpu":
            n = asr.cuda_device_count()
            check(n > 0, "faster-whisper 能看到显卡", "faster-whisper 看不到显卡",
                  "确认装的是带 CUDA 的版本：pip install -U faster-whisper ctranslate2")
            if n > 0 and not args.skip_gpu_test:
                ctx.log("   正在真实测试显卡转录（第一次会下载约 1.6GB 模型，请等待）…")
                try:
                    from faster_whisper import download_model
                    asr.setup_cuda_dlls()
                    model = str(cfg.get("model"))
                    path = download_model(asr.GPU_MODEL if model == "auto" else model, cache_dir=str(cfg.models_dir))
                    ok, err = asr.gpu_check(cfg, path, force=True)
                    check(ok, "显卡转录测试通过，转录会用显卡", f"显卡转录测试失败：{err}",
                          "按上面的提示安装运行库后，重新运行 dyknow doctor")
                except Exception as e:  # noqa: BLE001
                    check(False, "", f"模型下载失败：{str(e)[:100]}", "检查网络，或 dyknow config hf_mirror true")
    else:
        ctx.log("ℹ️ 没有检测到 NVIDIA 显卡：本地转录会用 CPU（慢），可以改用云端：dyknow config asr cloud")
    if mode == "cloud" or (mode == "auto" and not gpu_name):
        check(bool(str(cfg.get("siliconflow_api_key")).strip()), "已配置硅基流动 API Key",
              "没有配置硅基流动 API Key", "到 siliconflow.cn 注册后：dyknow config siliconflow_api_key <Key>")

    # 浏览器和登录
    if not args.skip_browser:
        try:
            with browser.Douyin(cfg, log=ctx.log) as dy:
                ctx.log(f"✅ 浏览器可用（{dy.channel_used}）")
                logged = dy.logged_in()
                check(logged, "抖音已登录", "抖音没有登录", "dyknow login")
                if logged:
                    try:
                        dy.api(browser.API_COLLECTION, method="POST", body={"count": "1", "cursor": "0"})
                        ctx.log("✅ 收藏接口可以直接读取")
                    except browser.ApiFail as e:
                        ctx.log(f"ℹ️ 收藏接口不能直接读取（{e.detail}），会自动改用页面方式，速度稍慢")
        except browser.BrowserError as e:
            check(False, "", browser.REASON_TEXT.get(e.reason, e.reason), NEXT_STEP.get(e.reason, ""))

    ctx.log("")
    if problems:
        return ctx.finish("doctor", ERROR, "按上面 ❌ 后面的提示逐个解决", problems=problems)
    return ctx.finish("doctor", OK, "一切正常，可以运行 dyknow update")


# ── 参数 ──────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dyknow", description="抖音收藏 → 本地 Markdown 知识库",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog="""
常用：
  dyknow login                     扫码登录（只需一次）
  dyknow update                    同步新收藏并转录
  dyknow link "分享文案或链接"       处理单个视频
  dyknow search 关键词 --limit 20   搜索并转录
  dyknow creator 博主主页链接        抓博主作品
  dyknow todo                      列出等 AI 整理的笔记
  dyknow frames                    给笔记补画面截图（给能看图的 AI 用）
  dyknow doctor                    检查显卡、登录、目录是否正常
""")
    p.add_argument("--version", action="version", version=f"dyknow {__version__}")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("login", help="扫码登录（只需一次）")
    s.add_argument("--force", action="store_true", help="清除登录状态重新扫码（换账号用）")

    s = sub.add_parser("update", help="同步新收藏并转录")
    s.add_argument("--limit", type=int, default=0, help="最多检查最新多少条收藏")
    s.add_argument("--no-transcribe", action="store_true", help="只建笔记，不转录")

    s = sub.add_parser("link", help="处理分享链接（可以一次传多个）")
    s.add_argument("text", nargs="+", help="抖音分享文案、链接或视频 ID")
    s.add_argument("--no-transcribe", action="store_true")

    s = sub.add_parser("search", help="按关键词搜索视频")
    s.add_argument("keyword", nargs="+")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--no-transcribe", action="store_true")

    s = sub.add_parser("creator", help="抓取博主的作品")
    s.add_argument("url", help="博主主页链接（或分享链接）")
    s.add_argument("--limit", type=int, default=30)
    s.add_argument("--no-transcribe", action="store_true")

    s = sub.add_parser("transcribe", help="转录还没转录的笔记")
    s.add_argument("--retry-failed", action="store_true", help="也重试转录失败的")
    s.add_argument("--limit", type=int, default=0)

    s = sub.add_parser("todo", help="列出等 AI 整理的笔记")
    s.add_argument("--limit", type=int, default=20)

    s = sub.add_parser("frames", help="给已有笔记补上画面截图（给能看图的 AI 用）")
    s.add_argument("ids", nargs="*", help="只处理这些视频（ID 或链接）；不填 = 所有还没有截图的笔记")
    s.add_argument("--limit", type=int, default=20, help="最多处理多少条（默认 20）")
    s.add_argument("--count", type=int, default=0, help="每条最多截几张（默认用配置 frames，没配置就是 8）")

    sub.add_parser("index", help="重新生成索引")

    s = sub.add_parser("config", help="查看或修改配置")
    s.add_argument("key", nargs="?")
    s.add_argument("value", nargs="?")

    s = sub.add_parser("migrate", help="把旧版 DyKnow 笔记复制成新格式（旧文件不动）")
    s.add_argument("old_dir")
    s.add_argument("--dry-run", action="store_true", help="只统计，不写文件")

    s = sub.add_parser("doctor", help="检查环境")
    s.add_argument("--skip-gpu-test", action="store_true")
    s.add_argument("--skip-browser", action="store_true")
    return p


HANDLERS = {
    "login": cmd_login, "update": cmd_update, "link": cmd_link, "search": cmd_search,
    "creator": cmd_creator, "transcribe": cmd_transcribe, "frames": cmd_frames, "todo": cmd_todo, "index": cmd_index,
    "config": cmd_config, "migrate": cmd_migrate, "doctor": cmd_doctor,
}


def main(argv: list[str] | None = None, cfg: Config | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # Windows 控制台中文
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return OK
    ctx = Ctx(cfg or load())
    try:
        return HANDLERS[args.command](ctx, args)
    except KeyboardInterrupt:
        return ctx.finish(args.command, ERROR, "已手动停止；重新运行同一条命令即可继续", reason="interrupted")
    except FileNotFoundError as e:
        ctx.log(f"❌ {e}")
        return ctx.finish(args.command, ERROR, reason="not_found", detail=str(e))
    except Exception as e:  # noqa: BLE001  兜底：不让 AI 面对一大段报错
        import traceback
        ctx._log.write(traceback.format_exc())
        msg = str(e).splitlines()[0][:150] if str(e) else type(e).__name__
        if "closed" in msg.lower():
            ctx.log("❌ 浏览器窗口被关掉了")
            return ctx.finish(args.command, ERROR, "重新运行刚才的命令，运行期间不要关闭弹出的浏览器窗口",
                              reason="browser_closed")
        ctx.log(f"❌ 出错：{type(e).__name__}: {msg}")
        return ctx.finish(args.command, ERROR,
                          f"重新运行一次；反复出现就把日志 {ctx.cfg.logs_dir / 'dyknow.log'} 发给开发者",
                          reason="error", detail=msg)
