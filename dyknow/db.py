"""
SQLite 状态管理 —— 记录每条收藏的同步状态

设计要点（断点续转）：
    1. status 字段语义：
        - pending_index      仅元数据，未进入转录流程
        - pending_download   待下载视频
        - pending_audio      视频已下载，待抽音频
        - pending_transcribe 音频已抽，待转录
        - transcribed        转录完成（最终态）
        - failed             失败（可重试）
    2. attempts 记录尝试次数，超过 max_attempts 才置 failed
    3. last_error 记录最后一次错误信息
    4. updated_at 字段用于审计进度
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import config
from .state import (
    TRANSCRIPT_DONE,
    TRANSCRIPT_FAILED,
    TRANSCRIPT_PENDING,
)


# 状态常量
STATUS_PENDING_INDEX = "pending_index"
STATUS_PENDING_DOWNLOAD = "pending_download"
STATUS_PENDING_AUDIO = "pending_audio"
STATUS_PENDING_TRANSCRIBE = "pending_transcribe"
STATUS_TRANSCRIBED = "transcribed"
STATUS_FAILED = "failed"

# 进入转录管线的状态（pending_* 系列 + failed 都可被重试）
TRANSCRIBE_PIPELINE_STATUSES = {
    STATUS_PENDING_INDEX,
    STATUS_PENDING_DOWNLOAD,
    STATUS_PENDING_AUDIO,
    STATUS_PENDING_TRANSCRIBE,
    STATUS_FAILED,
}

# 流水线阶段 → 转录维度状态
#
# 说明（v2）：数据库的 status 仍然是「转录流水线阶段」，语义不变，
# 以保证断点续转逻辑不被破坏；新的知识处理维度以独立字段保存。
PIPELINE_TO_TRANSCRIPT_STATUS = {
    STATUS_PENDING_INDEX: TRANSCRIPT_PENDING,
    STATUS_PENDING_DOWNLOAD: TRANSCRIPT_PENDING,
    STATUS_PENDING_AUDIO: TRANSCRIPT_PENDING,
    STATUS_PENDING_TRANSCRIBE: TRANSCRIPT_PENDING,
    STATUS_TRANSCRIBED: TRANSCRIPT_DONE,
    STATUS_FAILED: TRANSCRIPT_FAILED,
}


def pipeline_to_transcript_status(status: str) -> str | None:
    """把流水线阶段映射为 transcript_status（未知阶段返回 None）"""
    return PIPELINE_TO_TRANSCRIPT_STATUS.get(str(status or "").strip())


class SyncDB:
    """同步状态数据库"""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or config.db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            # 确保数据目录存在（首次运行时 data/ 可能尚未创建）
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._init_tables()
        return self._conn

    def _init_tables(self):
        """初始化表结构；兼容老库（无字段时自动迁移）"""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sync_log (
                aweme_id TEXT PRIMARY KEY,
                title TEXT,
                author TEXT,
                favorite_folder TEXT DEFAULT '',
                favorite_time TEXT DEFAULT '',
                cover_url TEXT DEFAULT '',
                video_url TEXT DEFAULT '',
                duration INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                note_path TEXT DEFAULT '',
                video_path TEXT DEFAULT '',
                audio_path TEXT DEFAULT '',
                attempts INTEGER DEFAULT 0,
                last_error TEXT DEFAULT '',
                transcript_status TEXT DEFAULT '',
                processing_status TEXT DEFAULT '',
                category TEXT DEFAULT '',
                transcript_hash TEXT DEFAULT '',
                summarized_from_hash TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sync_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_status ON sync_log(status);
            CREATE INDEX IF NOT EXISTS idx_updated ON sync_log(updated_at);
        """)
        # 旧库字段迁移：缺少的列依次 ALTER TABLE
        existing_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(sync_log)").fetchall()
        }
        migrations = [
            ("audio_path", "TEXT DEFAULT ''"),
            ("attempts", "INTEGER DEFAULT 0"),
            ("last_error", "TEXT DEFAULT ''"),
            # v2：知识处理维度字段（不改动既有 status 流水线语义）
            ("transcript_status", "TEXT DEFAULT ''"),
            ("processing_status", "TEXT DEFAULT ''"),
            ("category", "TEXT DEFAULT ''"),
            ("transcript_hash", "TEXT DEFAULT ''"),
            ("summarized_from_hash", "TEXT DEFAULT ''"),
        ]
        for col, decl in migrations:
            if col not in existing_cols:
                self.conn.execute(f"ALTER TABLE sync_log ADD COLUMN {col} {decl}")
        self.conn.commit()

    # ── 状态查改 ──────────────────────────────

    def exists(self, aweme_id: str) -> bool:
        """检查某条视频是否已记录"""
        row = self.conn.execute(
            "SELECT 1 FROM sync_log WHERE aweme_id = ?", (str(aweme_id),)
        ).fetchone()
        return row is not None

    def get_new_ids(self, aweme_ids: list[str]) -> list[str]:
        """从给定 ID 列表中筛出未记录的新 ID"""
        if not aweme_ids:
            return []
        placeholders = ",".join("?" for _ in aweme_ids)
        rows = self.conn.execute(
            f"SELECT aweme_id FROM sync_log WHERE aweme_id IN ({placeholders})",
            [str(i) for i in aweme_ids],
        ).fetchall()
        existing = {r["aweme_id"] for r in rows}
        return [i for i in aweme_ids if str(i) not in existing]

    def insert(self, aweme_id: str, title: str = "", author: str = "",
               favorite_folder: str = "", favorite_time: str = "",
               cover_url: str = "", video_url: str = "", duration: int = 0,
               status: str = "pending_index", note_path: str = ""):
        """插入一条新的同步记录"""
        now = datetime.now().isoformat()
        self.conn.execute("""
            INSERT INTO sync_log
                (aweme_id, title, author, favorite_folder, favorite_time,
                 cover_url, video_url, duration, status, note_path, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(aweme_id) DO UPDATE SET
                title=excluded.title, author=excluded.author,
                favorite_folder=excluded.favorite_folder, favorite_time=excluded.favorite_time,
                cover_url=excluded.cover_url, video_url=excluded.video_url,
                duration=excluded.duration, updated_at=excluded.updated_at
        """, (
            str(aweme_id), title, author, favorite_folder, favorite_time,
            cover_url, video_url, duration, status, note_path, now
        ))
        self.conn.commit()

    def update_status(self, aweme_id: str, status: str, **kwargs):
        """
        更新流水线状态和可选字段。

        status 仍表示转录流水线阶段（pending_* / transcribed / failed），
        以便断点续转逻辑完全不受影响；同时自动同步 transcript_status 维度。
        processing_status / category 等只在显式传入时更新。
        """
        now = datetime.now().isoformat()
        sets = ["status = ?", "updated_at = ?"]
        params = [status, now]

        # 允许额外更新的字段白名单
        allowed = {
            "note_path", "video_path", "audio_path",
            "title", "last_error", "attempts",
            "transcript_status", "processing_status", "category",
            "transcript_hash", "summarized_from_hash",
        }
        updates = dict(kwargs)
        if not updates.get("transcript_status"):
            derived = pipeline_to_transcript_status(status)
            if derived:
                updates["transcript_status"] = derived

        for key, val in updates.items():
            if key in allowed:
                sets.append(f"{key} = ?")
                params.append(val)

        params.append(str(aweme_id))
        self.conn.execute(
            f"UPDATE sync_log SET {', '.join(sets)} WHERE aweme_id = ?",
            params
        )
        self.conn.commit()

    def mark_attempt(self, aweme_id: str, stage: str, error: str = ""):
        """尝试一次转录管线：stage 表示进入的阶段，error 记录失败信息"""
        # attempts + 1，updated_at 更新
        now = datetime.now().isoformat()
        if error:
            self.conn.execute("""
                UPDATE sync_log
                SET status = ?, attempts = attempts + 1,
                    last_error = ?, updated_at = ?
                WHERE aweme_id = ?
            """, (stage, error[:500], now, str(aweme_id)))
        else:
            self.conn.execute("""
                UPDATE sync_log
                SET status = ?, attempts = attempts + 1,
                    last_error = '', updated_at = ?
                WHERE aweme_id = ?
            """, (stage, now, str(aweme_id)))
        self.conn.commit()

    def get_by_status(self, status: str, limit: int = 0) -> list[dict]:
        """按状态查询条目"""
        sql = "SELECT * FROM sync_log WHERE status = ? ORDER BY created_at ASC"
        if limit > 0:
            sql += f" LIMIT {limit}"
        rows = self.conn.execute(sql, (status,)).fetchall()
        return [dict(r) for r in rows]

    def get_pending_for_transcribe(
        self,
        include_failed: bool = False,
        limit: int = 0,
    ) -> list[dict]:
        """
        获取待转录的条目（支持断点续转）。
        include_failed=True 时把 failed 也纳入重试队列。
        """
        if include_failed:
            placeholders = ",".join("?" * len(TRANSCRIBE_PIPELINE_STATUSES))
            sql = (
                f"SELECT * FROM sync_log "
                f"WHERE status IN ({placeholders}) "
                f"ORDER BY updated_at ASC"
            )
            params: tuple = tuple(TRANSCRIBE_PIPELINE_STATUSES)
        else:
            statuses = TRANSCRIBE_PIPELINE_STATUSES - {STATUS_FAILED}
            placeholders = ",".join("?" * len(statuses))
            sql = (
                f"SELECT * FROM sync_log "
                f"WHERE status IN ({placeholders}) "
                f"ORDER BY updated_at ASC"
            )
            params = tuple(statuses)

        if limit > 0:
            sql += f" LIMIT {limit}"

        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def count_by_status(self, status: str) -> int:
        """统计某状态的条目数"""
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM sync_log WHERE status = ?", (status,)
        ).fetchone()
        return row["cnt"] if row else 0

    def get_status_breakdown(self) -> dict[str, int]:
        """返回各状态条目数（用于状态面板）"""
        rows = self.conn.execute(
            "SELECT status, COUNT(*) as cnt FROM sync_log GROUP BY status"
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    def total_count(self) -> int:
        """总条目数"""
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM sync_log").fetchone()
        return row["cnt"] if row else 0

    def reset_to_pending_index(self, aweme_id: str):
        """
        把一条记录重置为 pending_index（用于重跑）。

        注意：必须同时把 attempts 清零。否则条目此前若已累计到 --max-attempts，
        重跑时会被逐条「已达 max_attempts，跳过」，整批实际什么都不做，
        但日志仍会打印「转录结束」，极难发现。
        """
        self.update_status(
            aweme_id, STATUS_PENDING_INDEX,
            last_error="", audio_path="", attempts=0,
        )

    # ── v2：知识处理维度 ──────────────────────────

    def update_note_path(self, aweme_id: str, note_path: str):
        """移动笔记后同步数据库中的路径，保持一致"""
        if not note_path:
            return
        now = datetime.now().isoformat()
        self.conn.execute(
            "UPDATE sync_log SET note_path = ?, updated_at = ? WHERE aweme_id = ?",
            (str(note_path), now, str(aweme_id)),
        )
        self.conn.commit()

    def set_processing_status(self, aweme_id: str, value: str, category: str = ""):
        """写入 AI 知识处理维度状态（不触碰流水线 status）"""
        now = datetime.now().isoformat()
        sets = ["processing_status = ?", "updated_at = ?"]
        params: list = [value, now]
        if category:
            sets.append("category = ?")
            params.append(category)
        params.append(str(aweme_id))
        self.conn.execute(
            f"UPDATE sync_log SET {', '.join(sets)} WHERE aweme_id = ?",
            params,
        )
        self.conn.commit()

    def get_by_source_id(self, source_id: str) -> Optional[dict]:
        """按 source_id（即 aweme_id）取单条记录"""
        row = self.conn.execute(
            "SELECT * FROM sync_log WHERE aweme_id = ?", (str(source_id),)
        ).fetchone()
        return dict(row) if row else None

    def get_all(self, limit: int = 0) -> list[dict]:
        """取出全部记录（索引重建 / 归档扫描使用）"""
        sql = "SELECT * FROM sync_log ORDER BY created_at ASC"
        if limit > 0:
            sql += f" LIMIT {limit}"
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    def get_processing_breakdown(self) -> dict[str, int]:
        """AI 知识处理维度统计（空值计为 pending）"""
        rows = self.conn.execute(
            "SELECT processing_status, COUNT(*) AS cnt FROM sync_log "
            "GROUP BY processing_status"
        ).fetchall()
        return {(r["processing_status"] or "pending"): r["cnt"] for r in rows}

    # ── meta 读写 ──────────────────────────────

    def get_meta(self, key: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM sync_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO sync_meta (key, value) VALUES (?, ?)",
            (key, value)
        )
        self.conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# 便捷函数
_db_instance: SyncDB | None = None


def get_db() -> SyncDB:
    global _db_instance
    if _db_instance is None:
        _db_instance = SyncDB()
    return _db_instance
