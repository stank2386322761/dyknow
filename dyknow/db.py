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


class SyncDB:
    """同步状态数据库"""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or config.db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
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
            INSERT OR REPLACE INTO sync_log
                (aweme_id, title, author, favorite_folder, favorite_time,
                 cover_url, video_url, duration, status, note_path, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(aweme_id), title, author, favorite_folder, favorite_time,
            cover_url, video_url, duration, status, note_path, now
        ))
        self.conn.commit()

    def update_status(self, aweme_id: str, status: str, **kwargs):
        """更新状态和可选字段"""
        now = datetime.now().isoformat()
        sets = ["status = ?", "updated_at = ?"]
        params = [status, now]

        # 允许额外更新的字段白名单
        allowed = {
            "note_path", "video_path", "audio_path",
            "title", "last_error", "attempts",
        }
        for key, val in kwargs.items():
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
        """把一条记录重置为 pending_index（用于重跑）"""
        self.update_status(
            aweme_id, STATUS_PENDING_INDEX,
            last_error="", audio_path="",
        )

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
