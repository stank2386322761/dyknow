"""
增量同步协调器

阶段1: 爬取收藏列表 → 生成元数据笔记
阶段2: 下载视频 → 语音转录 → 更新笔记  （支持断点续转）

断点续转设计：
    - 每个 aweme_id 都有明确的 stage（pending_index / pending_download /
      pending_audio / pending_transcribe / transcribed / failed）
    - 重跑时按 stage 续跑：已 transcribed 跳过；已抽音的跳过下载直接转录
    - 实时写入 data/transcribe_progress.json 与 transcribe_run.log
    - Ctrl+C 优雅退出：把当前任务回退到对应 pending 阶段
    - 失败任务保留在 failed 状态，附 last_error，可加 --retry-failed 重试
"""

import asyncio
import json
import logging
import signal
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import config
from .db import (
    get_db, SyncDB,
    STATUS_PENDING_INDEX, STATUS_PENDING_DOWNLOAD,
    STATUS_PENDING_AUDIO, STATUS_PENDING_TRANSCRIBE,
    STATUS_TRANSCRIBED, STATUS_FAILED,
)
from .login import load_cookies
from .scraper import fetch as scrape_fetch
from .downloader import download, cleanup_video
from .transcriber import extract_audio, transcribe, is_available as transcribe_available
from .generator import generate_note, update_transcript

logger = logging.getLogger("dyknow.syncer")


class Syncer:

    def __init__(self):
        self.db: SyncDB = get_db()

    # ── 阶段1：同步元数据 ──────────────────────────

    def sync(
        self,
        count: int = 200,
        only_index: bool = False,
        only_transcribe: bool = False,
        transcribe: bool = False,
        full: bool = False,
    ) -> int:
        config.ensure_dirs()

        if full:
            print("全量同步模式")
        else:
            print(f"增量同步模式（已同步 {self.db.total_count()} 条）")

        try:
            if only_transcribe:
                return self._phase2_transcribe(count=count)

            cookies = load_cookies()
            if not cookies:
                print("需要登录...")
                from .login import login as do_login
                cookies = asyncio.run(do_login())
                if not cookies:
                    print("登录失败")
                    return 1

            items = asyncio.run(scrape_fetch(count=count))
            if not items:
                print("未获取到任何收藏")
                return 1

            if not full:
                all_ids = [item.aweme_id for item in items]
                new_ids = set(self.db.get_new_ids(all_ids))
                items = [item for item in items if item.aweme_id in new_ids]

            if not items:
                print("没有新收藏，已是最新")
                return 0

            print(f"\n阶段1：生成 {len(items)} 条笔记...")
            for i, item in enumerate(items, 1):
                try:
                    note_path = generate_note(
                        title=item.title,
                        aweme_id=item.aweme_id,
                        author=item.author,
                        author_id=item.author_id,
                        duration=item.duration,
                        likes=item.likes,
                        comments=item.comments,
                        shares=item.shares,
                        plays=item.plays,
                        cover_url=item.cover_url,
                        transcript="",
                    )
                    self.db.insert(
                        aweme_id=item.aweme_id,
                        title=item.title,
                        author=item.author,
                        cover_url=item.cover_url,
                        video_url=item.video_url,
                        duration=item.duration,
                        status=STATUS_PENDING_INDEX,
                        note_path=str(note_path),
                    )
                    if i % 20 == 0:
                        print(f"   [{i}/{len(items)}]")
                except Exception as e:
                    logger.error(f"生成笔记失败 ({item.aweme_id}): {e}")

            total = self.db.total_count()
            print(f"\n阶段1完成: 新增 {len(items)} 条，累计 {total} 条")
            print(f"笔记目录: {config.output_dir}")

            if only_index or not transcribe:
                print("\n提示: dyknow transcribe 可转录语音（支持断点续转）")
                return 0

            return self._phase2_transcribe()

        except Exception as e:
            logger.error(f"同步失败: {e}")
            traceback.print_exc()
            return 1

    # ── 阶段2：转录（带断点续转） ─────────────────────

    def _phase2_transcribe(
        self,
        count: int = 0,
        include_failed: bool = False,
        max_attempts: int = 3,
    ) -> int:
        """转录主流程，支持断点续转、Ctrl+C 安全退出、失败重试。"""
        if not transcribe_available():
            print("\n转录功能不可用（需安装转录后端 + 音频工具）")
            print("  方案1: pip install pywhispercpp     # whisper.cpp GGML，推荐⭐ 最稳定")
            print("  方案2: pip install faster-whisper   # CTranslate2加速，需GPU")
            print("  方案3: pip install qwen-asr        # Qwen3-ASR，中文方言优秀，需GPU")
            print("  方案4: pip install funasr           # SenseVoiceSmall，需torch")
            return 1

        pending = self.db.get_pending_for_transcribe(include_failed=include_failed)
        if count and count > 0:
            pending = pending[:count]
        if not pending:
            print("没有待转录的条目")
            return 0

        # 状态概览
        breakdown = self.db.get_status_breakdown()
        print("\n" + "=" * 60)
        print("转录状态总览")
        print("=" * 60)
        for st in (
            STATUS_PENDING_INDEX, STATUS_PENDING_DOWNLOAD,
            STATUS_PENDING_AUDIO, STATUS_PENDING_TRANSCRIBE,
            STATUS_TRANSCRIBED, STATUS_FAILED,
        ):
            print(f"  {st:<22} {breakdown.get(st, 0):>5}")
        print("-" * 60)
        print(f"本次将处理: {len(pending)} 条  (Ctrl+C 安全退出)")
        print("=" * 60 + "\n")

        # 进度文件 / 日志文件
        progress_file = config.data_dir / "transcribe_progress.json"
        log_file = config.data_dir / "transcribe_run.log"
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        progress_file.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "started_at": datetime.now().isoformat(),
                    "total": len(pending),
                    "current_index": 0,
                    "current_aweme_id": "",
                    "success": 0,
                    "failed": 0,
                    "skipped": 0,
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )

        # 状态：当前正在处理的任务（Ctrl+C 退出时回退 stage）
        state = {
            "current_aweme_id": "",
            "current_stage": "",   # 进入的 stage，Ctrl+C 时回退到前一阶段
            "interrupted": False,
        }

        def handle_sigint(signum, frame):
            """Ctrl+C 处理：标记中断、当前任务会回退 stage"""
            if state["interrupted"]:
                print("\n\n⚠️  二次中断，强制退出")
                sys.exit(1)
            state["interrupted"] = True
            print("\n\n⏸️  收到中断信号，等待当前任务完成后安全退出...")
            print("    （再次按 Ctrl+C 强制退出）")

        signal.signal(signal.SIGINT, handle_sigint)
        # Windows 下 SIGTERM 不一定可用，保留 POSIX 兼容
        try:
            signal.signal(signal.SIGTERM, handle_sigint)
        except (AttributeError, ValueError):
            pass

        success = 0
        failed = 0
        skipped = 0

        def log_line(msg: str):
            """同时输出到控制台 + 日志文件"""
            line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
            print(line)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")

        def save_progress(idx: int, current_id: str, s: int, f_cnt: int, sk: int):
            payload = json.dumps(
                {
                    "run_id": run_id,
                    "started_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                    "total": len(pending),
                    "current_index": idx,
                    "current_aweme_id": current_id,
                    "success": s,
                    "failed": f_cnt,
                    "skipped": sk,
                },
                ensure_ascii=False, indent=2,
            )
            # 百度同步盘可能锁定文件，重试几次
            import time as _time
            for attempt in range(5):
                try:
                    progress_file.write_text(payload, encoding="utf-8")
                    break
                except PermissionError:
                    if attempt < 4:
                        _time.sleep(1)
                    else:
                        log_line(f"⚠️ 进度文件写入失败（同步盘锁定），跳过本次进度保存")

        try:
            for i, item in enumerate(pending, 1):
                if state["interrupted"]:
                    break

                aweme_id = item["aweme_id"]
                title = item.get("title", "")[:40]
                video_url = item.get("video_url", "")
                note_path_str = item.get("note_path", "")
                current_status = item.get("status", STATUS_PENDING_INDEX)
                current_attempts = item.get("attempts", 0)

                note_path = self._resolve_note_path(aweme_id, note_path_str)
                state["current_aweme_id"] = aweme_id
                # 初始 stage 设为当前记录值，_process_one 进入子 stage 时会更新
                state["current_stage"] = current_status

                save_progress(i, aweme_id, success, failed, skipped)

                # 跳过已完成的
                if current_status == STATUS_TRANSCRIBED:
                    skipped += 1
                    log_line(f"[{i}/{len(pending)}] ⏭️  已转录，跳过: {title}")
                    continue

                # 超过最大尝试次数 → 跳过
                if current_attempts >= max_attempts and not include_failed:
                    skipped += 1
                    log_line(
                        f"[{i}/{len(pending)}] ⏭️  已达 max_attempts={max_attempts}，"
                        f"跳过: {title}"
                    )
                    continue

                # 无 video_url 无法继续
                if not video_url:
                    self.db.update_status(
                        aweme_id, STATUS_FAILED, last_error="无 video_url"
                    )
                    failed += 1
                    log_line(f"[{i}/{len(pending)}] ❌ 无 video_url: {title}")
                    continue

                log_line(f"[{i}/{len(pending)}] ▶ {title} (stage={current_status})")
                t0 = time.time()

                try:
                    ok, info = self._process_one(
                        aweme_id=aweme_id,
                        video_url=video_url,
                        note_path=note_path,
                        current_status=current_status,
                    )

                    if ok:
                        success += 1
                        cost = time.time() - t0
                        log_line(
                            f"[{i}/{len(pending)}] ✅ 完成 ({cost:.1f}s, "
                            f"{info.get('chars', 0)} 字符)"
                        )
                    else:
                        failed += 1
                        log_line(
                            f"[{i}/{len(pending)}] ❌ 失败: {info.get('error', '')}"
                        )

                except Exception as e:
                    failed += 1
                    err = f"{type(e).__name__}: {e}"
                    self.db.update_status(aweme_id, STATUS_FAILED, last_error=err)
                    log_line(f"[{i}/{len(pending)}] 💥 异常: {err}")

                # 清理可能的视频缓存
                cleanup_video(aweme_id)

        except KeyboardInterrupt:
            # 兜底（signal handler 多数情况会先处理）
            state["interrupted"] = True

        finally:
            # 中断时回退当前任务 stage
            if state["interrupted"] and state["current_aweme_id"]:
                # 查 db 取最后进入的 stage（更准确）
                current_db_stage = self._get_db_status(state["current_aweme_id"])
                self._rollback_stage(state["current_aweme_id"], current_db_stage)

        print("\n" + "=" * 60)
        print("转录结束")
        print("=" * 60)
        print(f"  ✅ 成功: {success}")
        print(f"  ❌ 失败: {failed}")
        print(f"  ⏭️  跳过: {skipped}")
        print(f"  📊 进度: data/transcribe_progress.json")
        print(f"  📋 日志: data/transcribe_run.log")
        print("=" * 60)
        return 0

    def _process_one(
        self,
        aweme_id: str,
        video_url: str,
        note_path: Optional[Path],
        current_status: str,
    ) -> tuple[bool, dict]:
        """
        处理单条视频：按当前 stage 决定从哪一步开始。
        返回 (success, info) 元组。
        """
        video_path: Optional[Path] = None
        audio_path: Optional[Path] = None

        # ── 决定从哪一步开始 ──
        need_download = current_status in (STATUS_PENDING_INDEX, STATUS_PENDING_DOWNLOAD, STATUS_FAILED)
        need_audio = need_download or current_status == STATUS_PENDING_AUDIO
        need_transcribe = need_audio or current_status == STATUS_PENDING_TRANSCRIBE

        try:
            # ── 1) 下载视频 ──
            if need_download:
                self.db.mark_attempt(aweme_id, STATUS_PENDING_DOWNLOAD)
                video_path = download(video_url, aweme_id)
                if not video_path:
                    self.db.update_status(
                        aweme_id, STATUS_FAILED, last_error="下载失败"
                    )
                    return False, {"error": "下载失败"}
                self.db.update_status(
                    aweme_id, STATUS_PENDING_DOWNLOAD,
                    video_path=str(video_path),
                )

            # ── 2) 抽音频 ──
            if need_audio:
                if video_path is None:
                    # 来自 pending_audio / pending_transcribe 阶段
                    video_path_str = self._get_video_path(aweme_id)
                    video_path = Path(video_path_str) if video_path_str else None

                if not video_path or not video_path.exists():
                    # 视频缓存可能已被清理 → 退回 pending_download 重下
                    self.db.update_status(
                        aweme_id, STATUS_FAILED, last_error="视频缓存缺失，需重下"
                    )
                    return False, {"error": "视频缓存缺失"}

                self.db.mark_attempt(aweme_id, STATUS_PENDING_AUDIO)
                audio_path = video_path.with_suffix(".wav")
                if not extract_audio(video_path, audio_path):
                    self.db.update_status(
                        aweme_id, STATUS_FAILED, last_error="音频提取失败"
                    )
                    return False, {"error": "音频提取失败"}
                self.db.update_status(
                    aweme_id, STATUS_PENDING_AUDIO,
                    audio_path=str(audio_path),
                )

            # ── 3) 转录 ──
            if need_transcribe:
                if audio_path is None:
                    audio_path_str = self._get_audio_path(aweme_id)
                    audio_path = Path(audio_path_str) if audio_path_str else None

                if not audio_path or not audio_path.exists():
                    self.db.update_status(
                        aweme_id, STATUS_FAILED, last_error="音频缓存缺失，需重抽"
                    )
                    return False, {"error": "音频缓存缺失"}

                self.db.mark_attempt(aweme_id, STATUS_PENDING_TRANSCRIBE)
                text = transcribe(audio_path)
                if not text:
                    self.db.update_status(
                        aweme_id, STATUS_FAILED, last_error="转录结果为空"
                    )
                    return False, {"error": "转录结果为空"}

                # 写入笔记（原子写）
                if note_path and note_path.exists():
                    update_transcript(note_path, text)

                # 清理音频缓存
                try:
                    audio_path.unlink(missing_ok=True)
                except Exception:
                    pass

                self.db.update_status(
                    aweme_id, STATUS_TRANSCRIBED, last_error="",
                )
                return True, {"chars": len(text)}

            return False, {"error": "未匹配任何 stage"}

        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            self.db.update_status(aweme_id, STATUS_FAILED, last_error=err)
            return False, {"error": err}

    def _get_video_path(self, aweme_id: str) -> Optional[str]:
        """从 db 取缓存的视频路径"""
        row = self.db.conn.execute(
            "SELECT video_path FROM sync_log WHERE aweme_id = ?", (aweme_id,)
        ).fetchone()
        return row["video_path"] if row and row["video_path"] else None

    def _get_audio_path(self, aweme_id: str) -> Optional[str]:
        """从 db 取缓存的音频路径"""
        row = self.db.conn.execute(
            "SELECT audio_path FROM sync_log WHERE aweme_id = ?", (aweme_id,)
        ).fetchone()
        return row["audio_path"] if row and row["audio_path"] else None

    def _get_db_status(self, aweme_id: str) -> str:
        """从 db 取当前 status（用于中断后回退 stage）"""
        row = self.db.conn.execute(
            "SELECT status FROM sync_log WHERE aweme_id = ?", (aweme_id,)
        ).fetchone()
        return row["status"] if row else STATUS_PENDING_INDEX

    def _rollback_stage(self, aweme_id: str, entered_stage: str):
        """
        Ctrl+C 中断时，把当前任务回退到对应 pending 阶段。
        这样下次跑会从断点处继续，而不是从头开始。
        """
        # 已进入的 stage 是当前进度，回退到上一个 stage
        rollback_map = {
            STATUS_PENDING_DOWNLOAD: STATUS_PENDING_INDEX,
            STATUS_PENDING_AUDIO: STATUS_PENDING_DOWNLOAD,
            STATUS_PENDING_TRANSCRIBE: STATUS_PENDING_AUDIO,
        }
        prev = rollback_map.get(entered_stage, STATUS_PENDING_INDEX)
        try:
            self.db.update_status(aweme_id, prev, last_error="用户中断")
            print(f"   ⏮️  已回退 {aweme_id} → {prev}")
        except Exception as e:
            logger.warning(f"回退 stage 失败: {e}")

    def _resolve_note_path(self, aweme_id: str, stored_path: str) -> Optional[Path]:
        """
        解析笔记路径：
        1) db 中存储的路径存在 → 直接用
        2) 否则在 output_dir 下递归查找 *_{aweme_id}.md（处理路径迁移）
        3) 找到后回写到 db
        4) 都找不到 → 返回 None
        """
        if stored_path:
            p = Path(stored_path)
            if p.exists():
                return p

        # 兜底：在 output_dir 递归查找
        suffix = f"_{aweme_id}.md"
        try:
            for md_file in config.output_dir.rglob(f"*{suffix}"):
                if md_file.is_file():
                    logger.info(
                        f"按 video_id 找回笔记: {aweme_id} → {md_file}"
                    )
                    # 回写到 db（保留当前 status）
                    current = self._get_db_status(aweme_id)
                    self.db.update_status(
                        aweme_id, current,
                        note_path=str(md_file),
                    )
                    return md_file
        except Exception as e:
            logger.warning(f"查找笔记失败 ({aweme_id}): {e}")

        return None
