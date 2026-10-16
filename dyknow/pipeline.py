"""流程：建笔记 → 下载 → 转录 → 写回笔记。

转录在主线程按顺序做（显卡一次只跑一个），下载在后台提前进行，
所以显卡不用等网络。Ctrl+C 随时可以停：没做完的笔记保持"待转录"，下次接着做。
"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import asr, media, note
from .config import Config
from .douyin import Comment, Video, parse_comment
from .store import Store

PREFETCH = 3


@dataclass
class Stats:
    found: int = 0           # 本次抓到的作品数
    new: int = 0             # 新建的笔记数
    existing: int = 0        # 已经有笔记的
    transcribed: int = 0
    no_speech: int = 0
    images: int = 0
    frames: int = 0          # 加了画面截图的笔记数
    failed: int = 0
    pending: int = 0         # 还没转录（中断或没开转录）
    interrupted: bool = False
    seconds: float = 0.0
    engine: str = ""
    engine_note: str = ""
    new_paths: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


@dataclass
class Job:
    video: Video
    path: Path


class Runner:
    def __init__(self, cfg: Config, log: Callable[[str], None] = print):
        self.cfg = cfg
        self.log = log
        self.store = Store(cfg.output_dir)
        self.template = note.load_template(str(cfg.get("template")))
        self.frames_n = int(cfg.get("frames") or 0)

    # ── 画面截图 ──────────────────────────────

    def save_frames(self, job: "Job", video_file: Path, stats: Stats) -> None:
        """从已下载的视频里截图写进笔记；失败只提示，不影响转录"""
        try:
            pr = media.probe(video_file)
            if pr is not None and not pr.has_video:
                return
            out = note.frames_dir(job.path, job.video.id)
            shots = media.extract_frames(video_file, out, self.frames_n,
                                         duration=(pr.duration if pr else job.video.duration_ms / 1000))
            if shots:
                text = note.replace_block(note.read(job.path), "frames", note.frames_block(job.path, shots))
                note.write_atomic(job.path, text)
                stats.frames += 1
        except Exception as e:  # noqa: BLE001
            self.log(f"   ⚠️ 截图失败（{job.video.id}）：{str(e)[:80]}")

    def save_images(self, v: Video, path: Path, stats: Stats) -> None:
        """图文作品：把图片下载下来放进笔记"""
        try:
            imgs = media.download_images(v.image_urls, note.frames_dir(path, v.id), self.frames_n)
            if imgs:
                block = note.frames_block(path, [(None, i) for i in imgs], kind="image")
                note.write_atomic(path, note.replace_block(note.read(path), "frames", block))
                stats.frames += 1
        except Exception as e:  # noqa: BLE001
            self.log(f"   ⚠️ 图片下载失败（{v.id}）：{str(e)[:80]}")

    def frames_only(self, jobs: list["Job"], stats: Stats) -> None:
        """只截图，不转录（dyknow frames 用）"""
        media_dir = self.cfg.cache_dir / "media"
        for i, job in enumerate(jobs, 1):
            label = f"[{i}/{len(jobs)}] {job.video.title[:30]}"
            if job.video.is_image:
                self.save_images(job.video, job.path, stats)
                self.log(f"{label} 🖼️ 图片")
                continue
            file = None
            try:
                file = media.download(job.video.media_urls, media_dir, job.video.id, require_audio=False,
                                      referer=job.video.referer)
                before = stats.frames
                self.save_frames(job, file, stats)
                self.log(f"{label} {'✅ 已截图' if stats.frames > before else '⚠️ 没有截到画面'}")
            except media.DownloadError as e:
                stats.failed += 1
                stats.failures.append(f"{job.video.id}：{e}")
                self.log(f"{label} ❌ {e}")
            finally:
                if file is not None:
                    Path(file).unlink(missing_ok=True)

    # ── 建笔记 ────────────────────────────────

    def add(self, videos: list[Video], folder: Path, stats: Stats, dy=None,
            day_from_published: bool = False) -> list[Job]:
        """为每个作品建笔记（已存在的跳过），返回需要转录的任务"""
        jobs: list[Job] = []
        n_comments = int(self.cfg.get("comments") or 0)
        stats.found += len(videos)
        for i, v in enumerate(videos, 1):
            existing = self.store.find(v.id)
            if existing:
                stats.existing += 1
                # 之前没转成功的，趁这次拿到了新的下载地址顺便转录
                if not v.is_image and v.media_urls and note.get_status(note.read(existing)) in (note.PENDING, note.FAILED):
                    jobs.append(Job(v, existing))
                continue
            comments: list[Comment] = []
            if dy is not None and n_comments > 0 and v.comments > 0:
                try:
                    comments = [c for c in (parse_comment(x) for x in dy.comments(v.id, n_comments)) if c]
                except Exception as e:  # noqa: BLE001  评论拿不到不影响主流程
                    self.log(f"   评论获取失败（{v.id}）：{str(e)[:60]}")
            day = v.published if (day_from_published and v.published) else None
            path, created = self.store.create(v, folder, self.template, comments, day=day)
            if not created:
                stats.existing += 1
                continue
            stats.new += 1
            stats.new_paths.append(str(path))
            if v.is_image:
                stats.images += 1
                if self.frames_n > 0 and v.image_urls:
                    self.save_images(v, path, stats)
            else:
                jobs.append(Job(v, path))
            if len(videos) > 10 and i % 10 == 0:
                self.log(f"   已建笔记 {i}/{len(videos)}")
        return jobs

    # ── 转录 ──────────────────────────────────

    def transcribe(self, jobs: list[Job], stats: Stats) -> None:
        if not jobs:
            return
        try:
            backend = asr.make(self.cfg, self.log)
        except asr.AsrError as e:
            stats.engine_note = str(e)
            stats.pending += len(jobs)
            self.log(f"⚠️ 不能转录：{e}")
            return
        if backend is None:
            stats.pending += len(jobs)
            stats.engine = "不转录（asr=none）"
            return
        try:
            if hasattr(backend, "load"):
                backend.load()
        except asr.AsrError as e:
            stats.engine_note = str(e)
            stats.pending += len(jobs)
            self.log(f"⚠️ 不能转录：{e}")
            return
        stats.engine = backend.describe()
        stats.engine_note = getattr(backend, "note", "") if getattr(backend, "device", "") != "cuda" else ""
        self.log(f"\n开始转录 {len(jobs)} 条，使用 {stats.engine}（Ctrl+C 可随时停止，下次接着做）")

        media_dir = self.cfg.cache_dir / "media"
        t0 = time.time()
        pool = ThreadPoolExecutor(max_workers=2)
        futures: dict[int, Future] = {}

        def submit(idx: int) -> None:
            if idx < len(jobs) and idx not in futures:
                j = jobs[idx]
                futures[idx] = pool.submit(media.download, j.video.media_urls, media_dir, j.video.id,
                                           referer=j.video.referer)

        done = 0
        try:
            for k in range(PREFETCH):
                submit(k)
            for idx, job in enumerate(jobs):
                submit(idx + PREFETCH)
                label = f"[{idx + 1}/{len(jobs)}] {job.video.title[:30]}"
                start = time.time()
                file: Path | None = None
                try:
                    file = futures.pop(idx).result()
                    if self.frames_n > 0:
                        self.save_frames(job, file, stats)
                    result = backend.transcribe(file)
                    self._write_transcript(job.path, result)
                    if result.is_empty:
                        stats.no_speech += 1
                        self.log(f"{label} → 无语音（纯音乐或没有人声）")
                    else:
                        stats.transcribed += 1
                        self.log(f"{label} ✅ {time.time() - start:.0f}秒，{len(result.text)}字")
                except (media.DownloadError, asr.AsrError) as e:
                    self._write_failed(job.path, str(e))
                    stats.failed += 1
                    stats.failures.append(f"{job.video.id}：{e}")
                    self.log(f"{label} ❌ {e}")
                except Exception as e:  # noqa: BLE001
                    msg = f"{type(e).__name__}: {str(e).splitlines()[0][:100] if str(e) else ''}"
                    self._write_failed(job.path, msg)
                    stats.failed += 1
                    stats.failures.append(f"{job.video.id}：{msg}")
                    self.log(f"{label} ❌ {msg}")
                finally:
                    if file is not None:
                        Path(file).unlink(missing_ok=True)
                done += 1
        except KeyboardInterrupt:
            stats.interrupted = True
            self.log("\n⏸️ 已停止。没转完的笔记保持「待转录」，运行 dyknow transcribe 接着做。")
        finally:
            for f in futures.values():
                f.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            stats.pending += len(jobs) - done
            stats.seconds += time.time() - t0
            for p in media_dir.glob("*.part") if media_dir.is_dir() else []:
                p.unlink(missing_ok=True)

    def _write_transcript(self, path: Path, result: asr.Transcript) -> None:
        text = note.read(path)
        if result.is_empty:
            text = note.replace_block(text, "transcript", "> [!note]- 转写\n> 没有识别到人声（可能是纯音乐）。")
            text = note.set_status(text, note.NO_SPEECH)
        else:
            text = note.replace_block(text, "transcript", note.transcript_block(result.paragraphs))
            if note.get_status(text) != note.ORGANIZED:
                text = note.set_status(text, note.TRANSCRIBED)
        note.write_atomic(path, text)

    def _write_failed(self, path: Path, reason: str) -> None:
        try:
            text = note.read(path)
        except OSError:
            return
        if note.get_status(text) in (note.TRANSCRIBED, note.ORGANIZED):
            return  # 已有转录的不降级
        reason = reason.replace("\n", " ")[:150]
        text = note.replace_block(text, "transcript", f"> [!note]- 转写\n> 转录失败：{reason}")
        note.write_atomic(path, note.set_status(text, note.FAILED))
