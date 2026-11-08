"""音视频文件：下载、检查音轨、截取画面。

下载先写 .part 临时文件，确认完整后再改名；中途断掉不会留下"看起来完整"的残缺文件。
下载后用 ffmpeg 检查有没有声音：抖音有些地址只有画面（DASH 分轨），这种文件转录必然失败，
要换下一个地址，而不是把它当成"没有人声"。

ffmpeg 优先用系统里装的，没有就用 imageio-ffmpeg 自带的（pip 安装时会一起装上）。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Referer": "https://www.douyin.com/",
}
MAX_BYTES = 1024 * 1024 * 1024
MIN_BYTES = 10 * 1024
NO_AUDIO = "文件只有画面没有声音"
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # Windows 上不弹黑色命令行窗口


class DownloadError(Exception):
    pass


# ── ffmpeg ────────────────────────────────────


def ffmpeg_exe() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def _run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=_NO_WINDOW)


@dataclass
class Probe:
    has_audio: bool
    has_video: bool
    duration: float


def probe(path: Path) -> Probe | None:
    """看文件里有没有声音 / 画面。没有 ffmpeg 时返回 None（无法判断）"""
    exe = ffmpeg_exe()
    if not exe:
        return None
    try:
        err = _run([exe, "-hide_banner", "-i", str(path)], timeout=60).stderr
    except (OSError, subprocess.TimeoutExpired):
        return None
    dur = 0.0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return Probe(
        has_audio=bool(re.search(r"Stream #[^\n]*: Audio:", err)),
        has_video=bool(re.search(r"Stream #[^\n]*: Video:(?![^\n]*attached pic)", err)),
        duration=dur,
    )


# ── 下载 ──────────────────────────────────────


def _headers(referer: str = "") -> dict:
    return {**HEADERS, "Referer": referer} if referer else HEADERS


def _fetch(url: str, dest: Path, timeout: int, referer: str = "") -> None:
    part = dest.with_name(dest.name + ".part")
    try:
        with requests.get(url, headers=_headers(referer), stream=True, timeout=timeout) as r:
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if "text/html" in ctype or "json" in ctype:
                raise DownloadError(f"返回的不是媒体文件（{ctype}）")
            expected = int(r.headers.get("content-length") or 0)
            got = 0
            with open(part, "wb") as f:
                for chunk in r.iter_content(256 * 1024):
                    if chunk:
                        f.write(chunk)
                        got += len(chunk)
                        if got > MAX_BYTES:
                            raise DownloadError("文件超过 1GB，跳过")
        if expected and got < expected:
            raise DownloadError(f"下载不完整（{got}/{expected}）")
        if got < MIN_BYTES:
            raise DownloadError(f"文件太小（{got} 字节），可能是错误页")
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)


def download(urls: list[str], dest_dir: Path, name: str, timeout: int = 60,
             require_audio: bool = True, referer: str = "") -> Path:
    """依次尝试候选地址，返回第一个可用的文件（require_audio 时必须带声音）"""
    if not urls:
        raise DownloadError("没有可下载的地址")
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / f"{name}.mp4"
    errors: list[str] = []
    for url in urls[:8]:
        try:
            _fetch(url, final, timeout, referer)
            if require_audio:
                pr = probe(final)
                if pr is not None and not pr.has_audio:
                    final.unlink(missing_ok=True)
                    raise DownloadError(NO_AUDIO)
            return final
        except (requests.RequestException, DownloadError, OSError) as e:
            errors.append(str(e).splitlines()[0][:80])
    if errors and all(e == NO_AUDIO for e in errors):
        raise DownloadError(f"试了 {len(errors)} 个下载地址，都{NO_AUDIO}")
    raise DownloadError("；".join(dict.fromkeys(errors[-3:])) or "下载失败")


def download_images(urls: list[str], dest_dir: Path, limit: int, timeout: int = 30,
                    referer: str = "") -> list[Path]:
    """下载图文作品的图片"""
    out: list[Path] = []
    if not urls or limit <= 0:
        return out
    _clear(dest_dir)
    for i, url in enumerate(urls[:limit], 1):
        try:
            with requests.get(url, headers=_headers(referer), timeout=timeout) as r:
                r.raise_for_status()
                ctype = r.headers.get("content-type", "")
                ext = ".webp" if "webp" in ctype or ".webp" in url else ".png" if "png" in ctype else ".jpg"
                if not ctype.startswith("image/") and len(r.content) < MIN_BYTES:
                    continue
                path = dest_dir / f"{i:02d}{ext}"
                path.write_bytes(r.content)
                out.append(path)
        except (requests.RequestException, OSError):
            continue
    return out


# ── 画面截取 ──────────────────────────────────


def _clear(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for f in d.iterdir():
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            f.unlink(missing_ok=True)


def _grab(exe: str, video: Path, out_dir: Path, vf: str, prefix: str) -> list[tuple[float, Path]]:
    pattern = out_dir / f"{prefix}%04d.jpg"
    proc = _run([exe, "-hide_banner", "-i", str(video), "-an", "-vf", vf + ",showinfo",
                 "-fps_mode", "vfr", "-q:v", "5", str(pattern)], timeout=600)
    times = [float(t) for t in re.findall(r"pts_time:\s*([\d.]+)", proc.stderr)]
    files = sorted(out_dir.glob(f"{prefix}*.jpg"))
    return list(zip(times, files)) if len(times) == len(files) else [(None, f) for f in files]  # type: ignore[misc]


def extract_frames(video: Path, out_dir: Path, limit: int, duration: float = 0.0,
                   scene: float = 0.3, width: int = 540) -> list[tuple[float | None, Path]]:
    """按画面变化截图（切镜头、换 PPT、换场景时各截一张），最多 limit 张。
    画面几乎不变的口播视频，改成按时间均匀截取。"""
    exe = ffmpeg_exe()
    if not exe:
        raise DownloadError("没有 ffmpeg，不能截取画面（pip install imageio-ffmpeg）")
    _clear(out_dir)
    scale = f"scale='min({width}\\,iw)':-2"
    shots = _grab(exe, video, out_dir, f"select='eq(n\\,0)+gt(scene\\,{scene})',{scale}", "s_")
    if len(shots) < max(2, limit // 2) and duration > 0:
        # 画面变化少（比如口播）：保留已截到的，再按时间均匀补几张
        need = limit - len(shots)
        fps = need / max(duration, 1.0)
        extra = _grab(exe, video, out_dir, f"fps={fps:.5f},{scale}", "u_")
        taken = [t for t, _ in shots if t is not None]
        for t, f in extra:
            if t is not None and any(abs(t - x) < 1.5 for x in taken):
                f.unlink(missing_ok=True)
                continue
            shots.append((t, f))
        shots.sort(key=lambda s: s[0] if s[0] is not None else 0)
    if len(shots) > limit:  # 太多就均匀挑 limit 张（保留第一张）
        step = len(shots) / limit
        keep = {int(i * step) for i in range(limit)}
        for i, (_, f) in enumerate(shots):
            if i not in keep:
                f.unlink(missing_ok=True)
        shots = [s for i, s in enumerate(shots) if i in keep]
    out: list[tuple[float | None, Path]] = []
    for i, (t, f) in enumerate(shots, 1):
        stamp = f"_{int(t) // 60:02d}-{int(t) % 60:02d}" if t is not None else ""
        target = out_dir / f"{i:02d}{stamp}.jpg"
        os.replace(f, target)
        out.append((t, target))
    return out
