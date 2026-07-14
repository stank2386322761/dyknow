"""
视频下载器 —— 流式下载 + 断点续传
"""

import logging
import time
from pathlib import Path

import requests

from .config import config

logger = logging.getLogger("dyknow.downloader")

# 通用请求头
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.douyin.com/",
    "Accept": "video/mp4",
}


def download(url: str, aweme_id: str, max_retries: int = 3) -> Path | None:
    """
    下载视频到缓存目录。

    参数:
        url: 视频直链
        aweme_id: 视频 ID（用作文件名）
        max_retries: 最大重试次数

    返回:
        本地文件路径，失败返回 None
    """
    if not url:
        logger.warning(f"视频 {aweme_id} 无下载链接")
        return None

    cache_dir = config.video_cache_dir
    output_path = cache_dir / f"{aweme_id}.mp4"

    # 断点续传：文件已存在且大小合理则直接返回
    if output_path.exists() and output_path.stat().st_size > 1024:
        size_mb = output_path.stat().st_size / 1024 / 1024
        logger.info(f"视频已存在，跳过下载: {output_path.name} ({size_mb:.1f} MB)")
        return output_path

    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=HEADERS, stream=True, timeout=60)
            resp.raise_for_status()

            total_size = int(resp.headers.get("content-length", 0))
            downloaded = 0

            with open(output_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

            size_mb = downloaded / 1024 / 1024

            # 校验：如果下载的文件太小（可能是错误页面），重试
            if total_size > 0 and downloaded < total_size * 0.9:
                logger.warning(f"下载不完整 ({downloaded}/{total_size})，重试...")
                continue

            logger.info(f"✅ 下载完成: {output_path.name} ({size_mb:.1f} MB)")
            return output_path

        except requests.RequestException as e:
            logger.warning(f"下载失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            time.sleep(3)

        except Exception as e:
            logger.error(f"下载异常: {e}")
            break

    # 清理不完整的文件
    if output_path.exists():
        output_path.unlink()

    return None


def cleanup_video(aweme_id: str):
    """删除缓存的视频文件"""
    video_path = config.video_cache_dir / f"{aweme_id}.mp4"
    if video_path.exists():
        video_path.unlink()
        logger.debug(f"视频缓存已清理: {video_path.name}")
