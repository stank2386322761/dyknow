"""
单视频解析器 —— 从聊天文本中提取抖音链接，下载+转录+生成MD

场景A：用户粘贴一段带抖音链接的文字，自动处理
"""

import logging
import re
import tempfile
from pathlib import Path
from typing import Optional

import requests

from .config import config
from .downloader import download
from .transcriber import extract_audio, transcribe, is_available as transcribe_available
from .generator import generate_note

logger = logging.getLogger("dyknow.parser")

# 抖音链接正则（覆盖常见格式）
DOUYIN_URL_RE = re.compile(
    r'https?://v\.douyin\.com/\S+'
    r'|https?://www\.douyin\.com/video/\d+'
    r'|https?://www\.iesdouyin\.com/share/video/\d+'
    r'|https?://www\.douyin\.com/note/\d+',
    re.IGNORECASE
)

# 抖音视频ID：19位数字
VIDEO_ID_RE = re.compile(r'(\d{19})')


def extract_url(text: str) -> str | None:
    """从任意文本中提取第一个抖音链接"""
    match = DOUYIN_URL_RE.search(text)
    return match.group(0).strip() if match else None


def extract_video_id(url_or_text: str) -> str | None:
    """从 URL 或文本中提取19位视频ID"""
    # 直接从URL匹配
    match = VIDEO_ID_RE.search(url_or_text)
    if match:
        return match.group(1)

    # 短链接需要解析重定向
    if 'v.douyin.com' in url_or_text:
        try:
            resp = requests.get(
                url_or_text.strip(),
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=10,
                allow_redirects=True,
            )
            match = VIDEO_ID_RE.search(resp.url)
            if match:
                return match.group(1)
        except Exception:
            pass

    return None


def fetch_metadata(video_id: str) -> dict:
    """
    通过抖音公开 API 获取视频元数据。
    返回 dict 包含 title/author/author_id/duration/likes/comments/shares/cover_url/video_url
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.douyin.com/",
    }

    # 多个 API 端点兜底
    endpoints = [
        f"https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={video_id}",
        f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={video_id}",
    ]

    for url in endpoints:
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                result = _parse_api_response(data)
                if result and result.get("title"):
                    return result
        except Exception:
            continue

    return {}


def _parse_api_response(data: dict) -> dict | None:
    """解析 API 响应 JSON"""
    # 适配不同响应结构
    aweme = None
    if "aweme_detail" in data:
        aweme = data["aweme_detail"]
    elif "item_list" in data and data["item_list"]:
        aweme = data["item_list"][0]
    elif "aweme_list" in data and data["aweme_list"]:
        aweme = data["aweme_list"][0]

    if not aweme:
        return None

    author = aweme.get("author", {})
    video = aweme.get("video", {})
    stats = aweme.get("statistics", {})
    play_addr = video.get("play_addr", {}) or video.get("play_addr_h264", {})
    cover = aweme.get("cover", {}) or video.get("cover", {})
    url_list = play_addr.get("url_list", [])
    cover_list = cover.get("url_list", [])

    return {
        "aweme_id": str(aweme.get("aweme_id", "")),
        "title": aweme.get("desc", "") or aweme.get("title", "") or "无标题",
        "author": author.get("nickname", ""),
        "author_id": author.get("sec_uid", "") or author.get("uid", ""),
        "duration": video.get("duration", 0) or aweme.get("duration", 0),
        "likes": stats.get("digg_count", 0),
        "comments": stats.get("comment_count", 0),
        "shares": stats.get("share_count", 0),
        "plays": stats.get("play_count", 0),
        "cover_url": cover_list[0] if cover_list else aweme.get("cover_url", ""),
        "video_url": url_list[0] if url_list else "",
    }


def parse_and_generate(
    text: str,
    output_dir: Path | None = None,
    transcribe_video: bool = True,
) -> Path | None:
    """
    核心函数：从文本中提取抖音链接，下载视频，转录，生成 MD。

    参数:
        text: 用户输入文本（可能包含抖音链接）
        output_dir: 笔记输出目录（None 则用默认配置）
        transcribe_video: 是否下载视频并转录

    返回:
        生成的 MD 文件路径，失败返回 None
    """
    # 1. 提取链接
    url = extract_url(text)
    if not url:
        logger.warning("未在文本中找到抖音链接")
        return None

    print(f"🔗 发现链接: {url}")

    # 2. 提取视频 ID
    video_id = extract_video_id(url)
    if not video_id:
        print("❌ 无法提取视频 ID")
        return None

    print(f"🆔 视频 ID: {video_id}")

    # 3. 获取元数据
    print("📋 获取视频信息...")
    meta = fetch_metadata(video_id)

    title = meta.get("title", "无标题")
    author = meta.get("author", "未知作者")
    video_url = meta.get("video_url", "")

    print(f"   标题: {title[:60]}")
    print(f"   作者: {author}")

    # 4. 下载视频 + 转录
    transcript = ""
    if transcribe_video and video_url and transcribe_available():
        print("📥 下载视频...")
        video_path = download(video_url, video_id)

        if video_path:
            print("🎙️ 转录中...")
            audio_path = video_path.with_suffix(".wav")
            if extract_audio(video_path, audio_path):
                transcript = transcribe(audio_path)
                audio_path.unlink(missing_ok=True)
                if transcript:
                    print(f"✅ 转录完成 ({len(transcript)} 字符)")
                else:
                    print("⚠️ 转录结果为空")
            else:
                print("❌ 音频提取失败")

            # 清理视频文件（只保留笔记）
            video_path.unlink(missing_ok=True)
        else:
            print("⚠️ 视频下载失败，只生成元数据笔记")
    elif not transcribe_available() and transcribe_video:
        print("⚠️ 转录功能不可用（推荐: pip install faster-whisper）")

    # 5. 生成笔记
    print("📝 生成笔记...")
    output = output_dir or config.output_dir
    note_path = generate_note(
        title=title,
        aweme_id=video_id,
        author=author,
        author_id=meta.get("author_id", ""),
        duration=meta.get("duration", 0),
        likes=meta.get("likes", 0),
        comments=meta.get("comments", 0),
        shares=meta.get("shares", 0),
        cover_url=meta.get("cover_url", ""),
        transcript=transcript,
        output_dir=output,
    )

    print(f"✅ 笔记已保存: {note_path}")
    return note_path


def parse_text_only(text: str) -> dict | None:
    """
    仅提取 URL 和元数据，不下载视频也不转录。
    用于快速获取视频信息，模型后续可以决定是否需要转录。

    返回 dict 或 None
    """
    url = extract_url(text)
    if not url:
        return None

    video_id = extract_video_id(url)
    if not video_id:
        return None

    meta = fetch_metadata(video_id)
    meta["url"] = url
    return meta
