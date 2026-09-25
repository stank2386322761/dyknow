"""
状态模型与幂等哈希

v1 用单个 status 同时表达「转录进度」和「AI 知识处理进度」，导致语义冲突：
    - 数据库用 pending_index / pending_download / ... / transcribed / failed
    - Markdown frontmatter 用 indexed / transcribed / done
两套值域不一致，且「AI 处理完成」在程序里根本没有落点。

v2 拆成两个维度：
    transcript_status: pending | done | failed
    processing_status: pending | done | needs_review

旧笔记按 LEGACY_STATUS_MAP 映射后继续可用，不做破坏性迁移。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

# ── 新状态值 ──────────────────────────────────

TRANSCRIPT_PENDING = "pending"
TRANSCRIPT_DONE = "done"
TRANSCRIPT_FAILED = "failed"

PROCESSING_PENDING = "pending"
PROCESSING_DONE = "done"
PROCESSING_NEEDS_REVIEW = "needs_review"

TRANSCRIPT_STATUSES = {TRANSCRIPT_PENDING, TRANSCRIPT_DONE, TRANSCRIPT_FAILED}
PROCESSING_STATUSES = {PROCESSING_PENDING, PROCESSING_DONE, PROCESSING_NEEDS_REVIEW}

# ── 旧 status → 新双维度 ──────────────────────
#
# 覆盖 Markdown frontmatter 历史取值（indexed / transcribed / done）
# 与数据库历史取值（pending_* / transcribed / failed）。

LEGACY_STATUS_MAP: dict[str, tuple[str, str]] = {
    # frontmatter 历史值
    "indexed": (TRANSCRIPT_PENDING, PROCESSING_PENDING),
    "transcribed": (TRANSCRIPT_DONE, PROCESSING_PENDING),
    "done": (TRANSCRIPT_DONE, PROCESSING_DONE),
    # 数据库管线阶段
    "pending": (TRANSCRIPT_PENDING, PROCESSING_PENDING),
    "pending_index": (TRANSCRIPT_PENDING, PROCESSING_PENDING),
    "pending_download": (TRANSCRIPT_PENDING, PROCESSING_PENDING),
    "pending_audio": (TRANSCRIPT_PENDING, PROCESSING_PENDING),
    "pending_transcribe": (TRANSCRIPT_PENDING, PROCESSING_PENDING),
    # 下载/转录中间态（老版本可能写出）
    "downloaded": (TRANSCRIPT_PENDING, PROCESSING_PENDING),
    "failed": (TRANSCRIPT_FAILED, PROCESSING_PENDING),
}

# 正文中判定「已有转录」的特征串
_PLACEHOLDER_MARKERS = ("待转录", "转录失败", "待模型", "待总结", "待生成")

TRANSCRIPT_SECTION_HEADING = "📝 完整转写"


# ── 转录文本规范化与哈希 ──────────────────────


def normalize_transcript(text: str) -> str:
    """
    规范化转录正文，用于计算稳定哈希。

    只消除不影响语义的差异（换行风格、首尾空白、行尾空白），
    这样「同一个转录」在 Windows / macOS 上得到相同哈希。
    """
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = "\n".join(line.rstrip() for line in t.split("\n"))
    return t.strip()


def transcript_hash(text: str, length: int = 16) -> str:
    """
    计算转录正文的 SHA-256 摘要（默认取前 16 位十六进制）。

    只基于转录正文本身，**不包含** updated_at / status / title 等易变字段，
    否则每次改动 frontmatter 都会导致假性失配。
    """
    norm = normalize_transcript(text)
    if not norm:
        return ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:length]


def is_placeholder(text: str) -> bool:
    """判断转录区块是否还是占位符（未转录 / 转录失败）"""
    if not text:
        return True
    t = text.strip()
    if not t:
        return True
    # 短且命中占位关键词 → 视为占位
    if len(t) <= 40 and any(k in t for k in _PLACEHOLDER_MARKERS):
        return True
    return False


# ── 状态解析 ──────────────────────────────────


def resolve_state(meta: dict, body: str = "") -> tuple[str, str]:
    """
    从 frontmatter（必要时结合正文）解析出 (transcript_status, processing_status)。

    优先级：
        1. 已存在的新字段 transcript_status / processing_status
        2. 旧字段 status 的映射
        3. 依据正文是否有真实转录推断
    """
    t = _clean(meta.get("transcript_status"), TRANSCRIPT_STATUSES)
    p = _clean(meta.get("processing_status"), PROCESSING_STATUSES)

    if t is None or p is None:
        legacy_t, legacy_p = _from_legacy(meta)
        if t is None:
            t = legacy_t
        if p is None:
            p = legacy_p

    # 完全没有状态信息 → 按正文推断
    if t is None or p is None:
        inferred_t, inferred_p = _infer_from_body(body)
        if t is None:
            t = inferred_t
        if p is None:
            p = inferred_p

    return t, p


def _clean(value, allowed: set) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lower()
    if not v:
        return None
    return v if v in allowed else None


def _from_legacy(meta: dict) -> tuple[str | None, str | None]:
    raw = meta.get("status")
    if raw is None:
        return None, None
    key = str(raw).strip().lower()
    if key in LEGACY_STATUS_MAP:
        return LEGACY_STATUS_MAP[key]
    return None, None


def _infer_from_body(body: str) -> tuple[str, str]:
    from .frontmatter import extract_section

    if not body:
        return TRANSCRIPT_PENDING, PROCESSING_PENDING
    section = extract_section(body, TRANSCRIPT_SECTION_HEADING)
    if is_placeholder(section):
        return TRANSCRIPT_PENDING, PROCESSING_PENDING
    # 有真实转录，但没写 processing_status → 尚未做 AI 处理
    return TRANSCRIPT_DONE, PROCESSING_PENDING


# ── 幂等判断 ──────────────────────────────────


def is_summary_up_to_date(meta: dict) -> bool:
    """
    摘要是否为最新。

    transcript_hash == summarized_from_hash 且 processing_status == done
        → 不需要重新摘要
    """
    t, p = resolve_state(meta)
    if p != PROCESSING_DONE:
        return False
    cur = str(meta.get("transcript_hash") or "").strip()
    done = str(meta.get("summarized_from_hash") or "").strip()
    if not cur or not done:
        return False
    return cur == done


def needs_resummarize(meta: dict, current_transcript: str) -> bool:
    """转录发生变化时（hash 失配）需要重新做 AI 知识处理"""
    cur = transcript_hash(current_transcript)
    if not cur:
        return False
    done = str(meta.get("summarized_from_hash") or "").strip()
    if not done:
        return True
    return done != cur


# ── 旧 status 兼容写出 ────────────────────────


def legacy_status_of(transcript_status: str, processing_status: str) -> str:
    """
    为新状态计算一个等价的旧 status 值，写入 frontmatter 以保持向后兼容。

    老工具/老索引只认识 indexed / transcribed / done，因此继续写出该字段；
    但新逻辑不再只依赖它做判断。
    """
    if transcript_status == TRANSCRIPT_FAILED:
        return "failed"
    if transcript_status != TRANSCRIPT_DONE:
        return "indexed"
    return "done" if processing_status == PROCESSING_DONE else "transcribed"


# ── 失败信息 ──────────────────────────────────

_ERROR_MAX_LEN = 300


def sanitize_error(message: str) -> str:
    """
    清理错误信息，准备写入数据库。

    避免把长堆栈塞进用户可见的笔记；数据库也只保留首行与有限长度。
    """
    if not message:
        return ""
    first = str(message).strip().splitlines()[0] if str(message).strip() else ""
    first = re.sub(r"\s+", " ", first).strip()
    return first[:_ERROR_MAX_LEN]


# ── source_id 恢复与笔记识别 ────────────────────
#
# source_id 是 v2 才引入的字段，v1 笔记只有 video_id / source。
# 直接按 source_id 判存在会让旧笔记从索引里消失，因此这里做安全恢复。
#
# 原则：**不伪造 ID**。确实恢复不出来就返回空串，由调用方按保守策略处理
# （仍安全读取与索引，但不参与去重 / 覆盖判断）。

# 从可信抖音 URL 中提取视频 ID，例如 https://www.douyin.com/video/7650813418760359203
_VIDEO_ID_IN_URL_RE = re.compile(r"(?:video|note|share/video)/(\d{10,25})")
# 任意 15-25 位连续数字（抖音 aweme_id 为 19 位）
_LONG_DIGITS_RE = re.compile(r"(\d{15,25})")
# v1 文件名末尾的 `<标题>_<aweme_id>` 形式：必须位于 stem 末尾，且前面有 _ 分隔
_FILENAME_ID_RE = re.compile(r"_(\d{15,25})$")

_ID_FIELDS = ("source_id", "video_id", "aweme_id", "awemeId", "item_id", "itemId")
_URL_FIELDS = ("source", "url", "share_url", "link", "original_url", "video_url")

# 可信抖音 host（含子域）。仅用于 URL 来源校验，绝不做整串 substring 匹配。
_TRUSTED_DOUYIN_HOSTS = ("douyin.com", "iesdouyin.com")


def trusted_douyin_host(url) -> str:
    """
    校验 URL 是否属于可信的抖音域名，返回规范化 hostname；不可信返回空串。

    用 URL parser 解析 hostname，而不是对整串做 substring 判断，
    因此下面这些都会被正确拒绝：

        https://notdouyin.com/video/123
        https://example.com/?next=https://www.douyin.com/video/123
        https://evil-example.com/?redirect=douyin.com

    下面这些会被接受（合法子域）：

        https://www.douyin.com/video/123
        https://v.douyin.com/abc/
        https://www.iesdouyin.com/share/video/123
    """
    if not url:
        return ""
    raw = str(url).strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = urlparse(raw).hostname or ""
    except ValueError:
        return ""
    host = host.lower().rstrip(".")
    if not host:
        return ""
    for base in _TRUSTED_DOUYIN_HOSTS:
        if host == base or host.endswith("." + base):
            return host
    return ""


def recover_source_id(meta: dict) -> str:
    """
    从 frontmatter 中恢复稳定视频 ID。

    优先级：
        1. 显式 ID 字段（source_id / video_id / aweme_id …），
           值必须是**完整的 15-25 位数字**
        2. URL 字段中 `/video/<id>` 形式的 ID（需先通过 host 校验）
        3. URL 中任意 15-25 位数字（同样需先通过 host 校验）

    无法恢复时返回空串（不伪造）。
    """
    if not isinstance(meta, dict):
        return ""

    for key in _ID_FIELDS:
        raw = meta.get(key)
        if raw is None:
            continue
        value = str(raw).strip()
        if not value:
            continue
        # 只接受**完整的** 15-25 位数字 ID。
        # 不做「任意长度纯数字」的兜底：`item_id` / `itemId` 是很通用的字段名，
        # 例如 `item_id: 123`，若允许短数字就会把普通 Markdown 误判成视频笔记。
        if _LONG_DIGITS_RE.fullmatch(value):
            return value

    for key in _URL_FIELDS:
        raw = meta.get(key)
        if not raw:
            continue
        value = str(raw)
        # 只有可信的抖音 URL 才允许从中提取 ID。
        # 普通站点的长数字（如 https://example.com/order/1234567890123456789）
        # 不得被当成视频 ID。
        if not trusted_douyin_host(value):
            continue
        m = _VIDEO_ID_IN_URL_RE.search(value)
        if m:
            return m.group(1)
        m = _LONG_DIGITS_RE.search(value)
        if m:
            return m.group(1)

    return ""


def recover_source_id_from_filename(path) -> str:
    """
    从文件名恢复视频 ID。

    本工具与 v1 生成的文件名格式均为 `<标题>_<aweme_id>.md`，
    旧笔记载体可能连 video_id 字段都没有，只能从文件名恢复。
    匹配不到时返回空串（不伪造）。
    """
    try:
        stem = Path(path).stem
    except (TypeError, ValueError):
        return ""
    # 只认 `<标题>_<aweme_id>` 这种**末尾**形式；
    # 文件名中间出现的长数字（如 `订单7650813418760359203记录.md`）不算。
    m = _FILENAME_ID_RE.search(stem)
    return m.group(1) if m else ""


def is_video_note(meta: dict) -> bool:
    """
    判断一条 Markdown 是否属于本工具（含 v1 版本）产出的视频笔记。

    用于把知识库里用户自己的普通笔记（README、灵感记录等）排除在索引之外，
    同时**不会**把缺少 source_id 的 v1 笔记排除掉。
    """
    if not isinstance(meta, dict):
        return False
    if recover_source_id(meta):
        return True
    if str(meta.get("platform") or "").strip().lower() == "douyin":
        return True
    for key in (
        "evidence_level",
        "transcript_status",
        "processing_status",
        "favorite_folder",
        "favorite_time",
    ):
        if meta.get(key) not in (None, ""):
            return True
    for key in _URL_FIELDS:
        if trusted_douyin_host(meta.get(key)):
            return True
    for key in ("douyin_url", "aweme_url"):
        if meta.get(key):
            return True
    return False
