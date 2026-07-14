"""
语音转录模块 —— pywhispercpp (whisper.cpp GGML)

  使用 ggml 模型进行语音转文字，CPU/GPU 通用，无需网络下载。

音频提取: ffmpeg → av 库回退
"""

import logging
import subprocess
import wave
from pathlib import Path

from .config import config

logger = logging.getLogger("dyknow.transcriber")


# ── 依赖检查 ──────────────────────────────


def _check_pywhispercpp() -> bool:
    """检查 pywhispercpp (whisper.cpp GGML) 是否可用"""
    try:
        import importlib.util
        return importlib.util.find_spec("pywhispercpp") is not None
    except Exception:
        return False


def _check_ffmpeg() -> bool:
    """检查 ffmpeg 命令是否可用"""
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _check_av() -> bool:
    """检查 av 库是否可用（ffmpeg 的备选方案）"""
    try:
        import av  # noqa: F401
        return True
    except ImportError:
        return False


# ── 音频提取 ─────────────────────────────


def extract_audio(video_path: Path, audio_path: Path) -> bool:
    """
    从视频提取 16kHz 单声道 WAV 音频。
    优先 ffmpeg，失败回退 av 库。
    """
    # 方案1：ffmpeg
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1",
        str(audio_path),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.debug("ffmpeg 不可用，尝试 av 库")

    # 方案2：av 库回退
    try:
        import av

        container = av.open(str(video_path))
        audio_stream = next(s for s in container.streams if s.type == "audio")
        resampler = av.audio.resampler.AudioResampler(
            format="s16", layout="mono", rate=16000
        )

        with wave.open(str(audio_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)

            for packet in container.demux(audio_stream):
                for frame in packet.decode():
                    frame.pts = None
                    resampled = resampler.resample(frame)
                    for rframe in resampled:
                        data = rframe.to_ndarray().tobytes()
                        wav_file.writeframesraw(data)

        container.close()
        return True

    except ImportError:
        logger.error("av 库未安装。请执行: pip install av")
        return False
    except StopIteration:
        logger.error("视频文件中未找到音频流")
        return False
    except Exception as e:
        logger.error(f"av 库提取音频失败: {e}")
        return False


# ── pywhispercpp (whisper.cpp GGML) ─────────

_ggml_model = None


def _get_ggml_model():
    """懒加载 whisper.cpp GGML 模型"""
    global _ggml_model
    if _ggml_model is None:
        from pywhispercpp.model import Model

        model_path = config.ggml_model_path
        logger.info("加载 GGML 模型（%s）...", model_path)
        _ggml_model = Model(model_path)
        logger.info("GGML 模型加载完成")
    return _ggml_model


def _transcribe_ggml(audio_path: Path) -> str:
    """使用 pywhispercpp (whisper.cpp) 转录"""
    try:
        model = _get_ggml_model()
        segments = model.transcribe(str(audio_path), language="zh")
        text = " ".join(s.text.strip() for s in segments)
        return text.strip()
    except Exception as e:
        logger.error(f"pywhispercpp 转录失败: {e}")
        return ""


# ── 统一入口 ──────────────────────────────


def get_active_backend() -> str | None:
    """返回当前使用的转录后端名称"""
    return "ggml" if _check_pywhispercpp() else None


def transcribe(audio_path: Path) -> str:
    """
    转录音频为文字，使用 pywhispercpp (whisper.cpp GGML)。
    """
    if not audio_path.exists():
        logger.error(f"音频文件不存在: {audio_path}")
        return ""

    if not _check_pywhispercpp():
        logger.error("pywhispercpp 未安装。请执行: pip install pywhispercpp")
        return ""

    logger.info("使用 GGML 转录: %s", audio_path.name)
    text = _transcribe_ggml(audio_path)
    if text:
        logger.info("转录成功, %d 字符", len(text))
    return text


def is_available() -> bool:
    """检查转录功能是否可用（pywhispercpp + 音频提取工具）"""
    has_audio_tool = _check_ffmpeg() or _check_av()
    return has_audio_tool and _check_pywhispercpp()
