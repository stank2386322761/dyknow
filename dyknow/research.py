"""找选题：热榜 + 跨平台找对标。

hot：抖音 / 知乎 / 微博 / B站 / 小红书热搜 → 热榜/热榜_日期.md
find：关键词在各平台搜索 → 只留最近 N 天 → 按点赞排序 → 对标/<关键词>/对标报告_日期.md
      --save N：每个平台前 N 条存成完整笔记（正文、图片、转录），沉淀到同一个文件夹
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import media, note, sites
from .browser import Browser, BrowserError
from .config import Config
from .douyin import Video
from .pipeline import Job, Runner, Stats
from .sites import Item, PLATFORM_NAMES
from .store import FOLDER_HOT, FOLDER_RESEARCH

FIND_DEFAULT = ["xhs", "zhihu", "bili", "douyin"]
HOT_DEFAULT = ["douyin", "zhihu", "weibo", "bili", "xhs"]
XHS_IMAGE_LIMIT = 9

REASON_TEXT = {
    "login": "没有登录",
    "blocked": "没有返回数据（可能被限流、要验证码或页面改版）",
    "parse": "抓到了数据但解析不出来（网站结构变了）",
    "error": "出错",
    "unsupported": "不支持",
}


@dataclass
class Outcome:
    results: dict[str, list] = field(default_factory=dict)   # 平台 → Item / HotItem 列表
    errors: dict[str, str] = field(default_factory=dict)     # 平台 → 原因
    reasons: dict[str, str] = field(default_factory=dict)    # 平台 → login / blocked / …
    notes: dict[str, str] = field(default_factory=dict)      # 平台 → 补充说明
    dropped_old: dict[str, int] = field(default_factory=dict)
    saved: dict[str, Path] = field(default_factory=dict)     # 内容 ID → 本地笔记
    stats: Stats = field(default_factory=Stats)
    report: Path | None = None
    raw_dir: Path | None = None


def _raw_dir(cfg: Config, kind: str) -> Path:
    root = cfg.cache_dir / "raw"
    if root.is_dir():  # 只留最近 5 次的原始数据
        for old in sorted(root.iterdir())[:-4]:
            shutil.rmtree(old, ignore_errors=True)
    return root / f"{datetime.now():%Y%m%d_%H%M%S}_{kind}"


def _niche(cfg: Config) -> str:
    n = str(cfg.get("niche") or "").strip()
    return n or "还没设置，可以让用户运行 dyknow config niche \"我的账号方向\""


def _md(s: str) -> str:
    return str(s or "").replace("|", "｜").replace("\n", " ").replace("[", "（").replace("]", "）")


# ── 热榜 ──────────────────────────────────────


def hot(cfg: Config, plats: list[str], limit: int, log: Callable[[str], None] = print) -> Outcome:
    out = Outcome(raw_dir=_raw_dir(cfg, "hot"))
    b = Browser(cfg, log=log, start_url="")
    b.raw_dir = out.raw_dir
    with b:
        b.minimize()
        for p in plats:
            log(f"【{PLATFORM_NAMES[p]}】获取热榜…")
            try:
                r = sites.module(p).hot(b, cfg, limit, log)
            except BrowserError:
                raise
            except Exception as e:  # noqa: BLE001  一个平台出错不影响其他平台
                r = sites.Result(reason="error", detail=f"{type(e).__name__}: {str(e)[:80]}")
            if r.reason:
                out.reasons[p] = r.reason
                out.errors[p] = REASON_TEXT.get(r.reason, r.reason) + (f"（{r.detail}）" if r.detail else "")
                log(f"   ⚠️ {out.errors[p]}")
            else:
                out.results[p] = r.hot
                log(f"   拿到 {len(r.hot)} 条")
    out.report = _write_hot(cfg, out)
    return out


def _write_hot(cfg: Config, out: Outcome) -> Path:
    now = datetime.now()
    counts = " · ".join(f"{PLATFORM_NAMES[p]} {len(v)}" for p, v in out.results.items())
    lines = [f"# 热榜 {now:%Y-%m-%d}", "", f"> {counts}（{now:%H:%M} 抓取）", ""]
    if out.errors:
        lines += ["> 没拿到：" + "；".join(f"{PLATFORM_NAMES[p]}{e}" for p, e in out.errors.items()), ""]
    lines += [
        "## 和我有关的热点",
        f"<!-- AI：从下面的热榜里挑出 3-5 个能和我的方向（{_niche(cfg)}）结合的热点。每个写：热点是什么；"
        "我能做的具体选题标题；为什么现在做（热点一般 1-3 天就过去）。"
        "选题先过三关：会有人主动搜吗、能解决一个具体问题吗、看完会有\"终于说清楚了\"的感觉吗。"
        "没有能结合的就写\"今天没有合适的\"，不要硬蹭。 -->",
        "",
    ]
    for p, rows in out.results.items():
        lines += [f"## {PLATFORM_NAMES[p]}", ""]
        for h in rows:
            title = f"[{_md(h.title)}]({h.url})" if h.url else _md(h.title)
            extra = " · ".join(x for x in (h.heat, h.label) if x)
            lines.append(f"{h.rank}. {title}" + (f" · {extra}" if extra else ""))
        lines.append("")
    path = cfg.output_dir / FOLDER_HOT / f"热榜_{now:%Y-%m-%d}.md"
    note.write_atomic(path, "\n".join(lines))
    return path


# ── 找对标 ────────────────────────────────────


def find(cfg: Config, keyword: str, plats: list[str], days: int, limit: int, save_n: int,
         log: Callable[[str], None] = print, transcribe: bool = True) -> Outcome:
    out = Outcome(raw_dir=_raw_dir(cfg, "find"))
    runner = Runner(cfg, log)
    folder = runner.store.folder(FOLDER_RESEARCH, keyword)
    jobs: list[Job] = []
    b = Browser(cfg, log=log, start_url="")
    b.raw_dir = out.raw_dir
    with b:
        b.minimize()
        for p in plats:
            mod = sites.module(p)
            log(f"\n【{PLATFORM_NAMES[p]}】搜索「{keyword}」…")
            try:
                r = mod.search(b, cfg, keyword, limit, days, log)
            except BrowserError:
                raise
            except Exception as e:  # noqa: BLE001
                r = sites.Result(reason="error", detail=f"{type(e).__name__}: {str(e)[:80]}")
            if r.reason:
                out.reasons[p] = r.reason
                out.errors[p] = REASON_TEXT.get(r.reason, r.reason) + (f"（{r.detail}）" if r.detail else "")
                log(f"   ⚠️ {out.errors[p]}")
                continue
            kept, dropped = [], 0
            for it in r.items:
                if sites.within_days(it.published, days) is False:
                    dropped += 1
                else:
                    kept.append(it)
            kept.sort(key=lambda i: i.score, reverse=True)
            out.results[p] = kept[:limit]
            out.dropped_old[p] = dropped
            if r.note:
                out.notes[p] = r.note
            log(f"   拿到 {len(r.items)} 条，去掉 {days} 天以前的 {dropped} 条，保留点赞最高的 {len(out.results[p])} 条")
            if save_n > 0:
                jobs += _save(cfg, b, runner, mod, out.results[p][:save_n], folder, out, log)
    if transcribe and jobs:
        runner.transcribe(jobs, out.stats)
    out.report = _write_find(cfg, keyword, days, out, folder)
    return out


def _save(cfg, b, runner: Runner, mod, items: list[Item], folder: Path, out: Outcome, log) -> list[Job]:
    """把前几条存成完整笔记：补正文、下载图片，需要转录的返回任务"""
    jobs: list[Job] = []
    for it in items:
        if it.platform == "douyin":
            v: Video = it.extra["video"]
            jobs += runner.add([v], folder, out.stats, dy=b)
            if runner.store.find(v.id):
                out.saved[it.id] = runner.store.find(v.id)
            continue
        try:
            mod.detail(b, cfg, it)
        except BrowserError:
            raise
        except Exception as e:  # noqa: BLE001  拿不到正文就只存搜索结果里的摘要
            log(f"   正文获取失败（{it.title[:20]}）：{str(e)[:60]}")
        path, created = runner.store.create_item(it, folder, runner.template)
        out.saved[it.id] = path
        if not created:
            out.stats.existing += 1
            continue
        out.stats.new += 1
        out.stats.new_paths.append(str(path))
        if it.platform == "xhs" and it.images:
            imgs = media.download_images(it.images, note.frames_dir(path, it.id), XHS_IMAGE_LIMIT,
                                         referer="https://www.xiaohongshu.com/")
            if imgs:
                block = note.frames_block(path, [(None, i) for i in imgs], kind="image")
                note.write_atomic(path, note.replace_block(note.read(path), "frames", block))
                out.stats.frames += 1
        if it.media_urls:
            jobs.append(Job(Video(id=it.id, desc=it.title, media_urls=it.media_urls,
                                  duration_ms=it.duration_ms, referer=it.referer), path))
        b._pause()
    return jobs


def _write_find(cfg: Config, keyword: str, days: int, out: Outcome, folder: Path) -> Path:
    now = datetime.now()
    counts = " · ".join(f"{PLATFORM_NAMES[p]} {len(v)} 条" for p, v in out.results.items())
    span = f"最近 {days} 天" if days > 0 else "不限时间"
    lines = [f"# 对标：{keyword}", "",
             f"> {now:%Y-%m-%d} · {span} · 每个平台按点赞从高到低（没有点赞数的按收藏、播放）", f"> {counts}", ""]
    if out.errors:
        lines += ["> 没抓到：" + "；".join(f"{PLATFORM_NAMES[p]}{e}" for p, e in out.errors.items()), ""]
    for p, n in out.notes.items():
        lines += [f"> {PLATFORM_NAMES[p]}：{n}", ""]
    lines += [
        "## 选题建议",
        "<!-- AI：根据下面各平台的高赞内容，给出 3-5 个我可以做的选题。每个选题写：\n"
        "1）标题：用别人会搜的说法；\n2）依据：引用下面哪个平台第几条、多少赞，说明这个需求真实存在；\n"
        f"3）我的角度：和这些对标比，我能讲出什么不一样的（我的方向：{_niche(cfg)}）。\n"
        "每个选题先过三关，过不了就换：会有人主动搜吗？能解决一个具体问题吗？看完会有\"终于说清楚了\"的感觉吗？"
        "标题的钩子后面必须能兑现，不写只有噱头的标题。 -->",
        "",
    ]
    for p, items in out.results.items():
        lines += [f"## {PLATFORM_NAMES[p]}（{len(items)}）", "",
                  "| # | 标题 | 类型 | 作者 | 👍 | ⭐ | 💬 | 发布 |", "|---|---|---|---|---:|---:|---:|---|"]
        for n, it in enumerate(items, 1):
            title = f"[{_md(it.title)[:40]}]({it.url})"
            local = out.saved.get(it.id)
            if local:
                title += f" [[{local.stem}\\|📝]]"
            likes = note.fmt_count(it.likes) if it.likes else (f"▶{note.fmt_count(it.views)}" if it.views else "0")
            lines.append(f"| {n} | {title} | {it.kind} | {_md(it.author)[:12]} | {likes} | "
                         f"{note.fmt_count(it.collects) if it.collects else ''} | "
                         f"{note.fmt_count(it.comments) if it.comments else ''} | {it.published or '?'} |")
        excerpts = [f"{n}. {_md(it.title)[:40]}：{_md(it.excerpt)[:120]}" for n, it in enumerate(items, 1) if it.excerpt]
        if excerpts:
            lines += ["", "> [!note]- 摘要"] + [f"> {e}" for e in excerpts]
        lines.append("")
    if any(it.published == "" for items in out.results.values() for it in items):
        lines += ["发布时间是 `?` 的拿不到发布日期，没有按时间过滤。", ""]
    path = folder / f"对标报告_{now:%Y-%m-%d}.md"
    note.write_atomic(path, "\n".join(lines))
    return path
