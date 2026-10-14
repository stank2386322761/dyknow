"""语音转文字。

local：faster-whisper，自动用 NVIDIA 显卡（float16），没有显卡用 CPU。
       显卡用不上时会明确说出原因，不会悄悄退回 CPU 还说"用上了"。
cloud：硅基流动 SenseVoice（有免费额度），适合没有显卡的电脑。

输出统一是"段落列表"：[(开始秒数或 None, 文字), ...]
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import Config

SILICONFLOW_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"
SILICONFLOW_MODEL = "FunAudioLLM/SenseVoiceSmall"
GPU_MODEL = "large-v3-turbo"
CPU_MODEL = "small"
CLOUD_CHUNK_SECONDS = 300


class AsrError(Exception):
    pass


@dataclass
class Transcript:
    paragraphs: list[tuple[float | None, str]] = field(default_factory=list)
    language: str = ""
    duration: float = 0.0

    @property
    def text(self) -> str:
        return "".join(t for _, t in self.paragraphs)

    @property
    def is_empty(self) -> bool:
        return len(re.sub(r"[\W_]+", "", self.text)) < 4


# ── 文本整理 ──────────────────────────────────

_ASCII_WORD_END = re.compile(r"[A-Za-z0-9]$")
_ASCII_WORD_START = re.compile(r"^[A-Za-z0-9]")


def smart_join(a: str, b: str) -> str:
    a, b = a.rstrip(), b.strip()
    if not a:
        return b
    if not b:
        return a
    if _ASCII_WORD_END.search(a) and _ASCII_WORD_START.search(b):
        return a + " " + b
    if a[-1] in ",.!?;:" and _ASCII_WORD_START.search(b):
        return a + " " + b
    return a + b


def to_simplified(text: str) -> str:
    try:
        from zhconv import convert  # 可选依赖
    except ImportError:
        return text
    return convert(text, "zh-cn")


def group_segments(segments: list[tuple[float, float, str]], max_chars: int = 220,
                   max_seconds: float = 45.0, pause: float = 1.2) -> list[tuple[float | None, str]]:
    """把 whisper 的短句合并成段落：停顿较长、段落过长或时间过长时另起一段"""
    paras: list[tuple[float | None, str]] = []
    cur_start: float | None = None
    cur_text = ""
    last_end = 0.0
    for start, end, text in segments:
        text = (text or "").strip()
        if not text:
            continue
        if cur_text and (
            len(cur_text) >= max_chars
            or (start - (cur_start or 0)) >= max_seconds
            or (start - last_end >= pause and len(cur_text) >= 60)
        ):
            paras.append((cur_start, cur_text))
            cur_start, cur_text = None, ""
        if cur_start is None:
            cur_start = start
        cur_text = smart_join(cur_text, text)
        last_end = end
    if cur_text:
        paras.append((cur_start, cur_text))
    return paras


def split_plain(text: str, offset: float | None = None, max_chars: int = 220) -> list[tuple[float | None, str]]:
    """没有时间戳的长文本，按句号切成段落"""
    sentences = []
    for s in re.split(r"(?<=[。！？!?\.])\s*", text.strip()):
        # 没有标点的长文本（旧版小模型常见）按长度硬切
        while len(s) > max_chars:
            sentences.append(s[:max_chars])
            s = s[max_chars:]
        sentences.append(s)
    paras, cur = [], ""
    for s in sentences:
        if not s:
            continue
        if cur and len(cur) + len(s) > max_chars:
            paras.append(cur)
            cur = ""
        cur = smart_join(cur, s)
    if cur:
        paras.append(cur)
    return [(offset if i == 0 else None, p) for i, p in enumerate(paras)]


# ── 显卡环境 ──────────────────────────────────


def setup_cuda_dlls() -> list[str]:
    """Windows：把 pip 安装的 NVIDIA 运行库（cuBLAS / cuDNN）加入 DLL 搜索路径"""
    added: list[str] = []
    if os.name != "nt":
        return added
    for pkg in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc", "nvidia.cuda_runtime"):
        try:
            spec = importlib.util.find_spec(pkg)
        except (ImportError, ValueError):
            spec = None
        if not spec or not spec.submodule_search_locations:
            continue
        bin_dir = Path(list(spec.submodule_search_locations)[0]) / "bin"
        if bin_dir.is_dir():
            try:
                os.add_dll_directory(str(bin_dir))
            except (OSError, AttributeError):
                pass
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            added.append(str(bin_dir))
    return added


def cuda_device_count() -> int:
    try:
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count())
    except Exception:  # noqa: BLE001
        return 0


_PROBE_SCRIPT = r"""
import sys
sys.path.insert(0, sys.argv[3])
from dyknow.asr import setup_cuda_dlls
setup_cuda_dlls()
import numpy as np
from faster_whisper import WhisperModel
m = WhisperModel(sys.argv[1], device="cuda", compute_type="float16", download_root=sys.argv[2])
segs, info = m.transcribe(np.zeros(32000, dtype=np.float32), language="zh")
list(segs)
print("GPU_OK")
"""


def gpu_check(cfg: Config, model: str, force: bool = False) -> tuple[bool, str]:
    """在子进程里真跑一次显卡推理。
    缺少 cuDNN 等运行库时 faster-whisper 会让整个进程直接崩溃，所以不能在主进程里试。
    结果按 (模型, ctranslate2 版本) 缓存。"""
    try:
        import ctranslate2
        ver = getattr(ctranslate2, "__version__", "?")
    except ImportError:
        return False, "没有安装 faster-whisper"
    key = f"{model}|{ver}"
    cache = cfg.home / "gpu_check.json"
    if not force:
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("key") == key:
                return bool(data.get("ok")), str(data.get("error") or "")
        except (OSError, ValueError):
            pass
    pkg_root = str(Path(__file__).resolve().parent.parent)
    try:
        proc = subprocess.run([sys.executable, "-c", _PROBE_SCRIPT, model, str(cfg.models_dir), pkg_root],
                              capture_output=True, text=True, timeout=600, env=os.environ.copy())
        out = (proc.stdout or "") + (proc.stderr or "")
        ok = "GPU_OK" in out
        err = "" if ok else _probe_error(out, proc.returncode)
    except subprocess.TimeoutExpired:
        ok, err = False, "显卡测试超时"
    cfg.home.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"key": key, "ok": ok, "error": err}, ensure_ascii=False), encoding="utf-8")
    return ok, err


def _probe_error(out: str, code: int) -> str:
    low = out.lower()
    if "cudnn" in low:
        return "缺少 cuDNN 运行库 → 运行：pip install nvidia-cudnn-cu12==9.* nvidia-cublas-cu12"
    if "cublas" in low:
        return "缺少 cuBLAS 运行库 → 运行：pip install nvidia-cublas-cu12"
    if "out of memory" in low:
        return "显存不足 → 把 model 改成 small 或 medium"
    if "cuda driver" in low or "driver version" in low:
        return "显卡驱动太旧 → 更新 NVIDIA 驱动"
    last = [ln for ln in out.strip().splitlines() if ln.strip()]
    return (last[-1][:160] if last else f"测试进程退出码 {code}")


# ── 后端 ──────────────────────────────────────


# 中文提示词：whisper 会模仿提示词的写法，加上标点和简体，输出就会带标点、用简体
ZH_PROMPT = "以下是普通话的视频内容，可能夹杂English单词，使用简体中文，并加上逗号、句号和问号。"


def decode(path: Path):
    """读出 16kHz 音频；文件没有声音时给出能看懂的错误，而不是 IndexError"""
    try:
        from faster_whisper import decode_audio
    except ImportError:
        raise AsrError("没有安装 faster-whisper → 运行：pip install faster-whisper")
    try:
        return decode_audio(str(path), sampling_rate=16000)
    except (IndexError, StopIteration):
        raise AsrError("文件里没有声音轨（只有画面）")
    except Exception as e:  # noqa: BLE001
        raise AsrError(f"读取音频失败：{type(e).__name__}: {str(e)[:80]}")


class LocalWhisper:
    name = "local"

    def __init__(self, cfg: Config, log: Callable[[str], None] = print):
        self.cfg = cfg
        self.log = log
        self.model = None
        self.device = ""
        self.model_name = ""
        self.note = ""  # 显卡没用上的原因

    def load(self) -> None:
        if self.model is not None:
            return
        setup_cuda_dlls()
        # 镜像地址必须在导入 huggingface_hub 之前设置，导入时就读取了
        if self.cfg.get("hf_mirror") and not os.environ.get("HF_ENDPOINT"):
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        try:
            from faster_whisper import WhisperModel, download_model
        except ImportError:
            raise AsrError("没有安装 faster-whisper → 运行：pip install faster-whisper")

        want = str(self.cfg.get("device") or "auto")
        model_cfg = str(self.cfg.get("model") or "auto")
        use_cuda = want == "cuda" or (want == "auto" and cuda_device_count() > 0)
        if want == "auto" and not use_cuda:
            self.note = "没有检测到 NVIDIA 显卡（或没装显卡版运行库）"
        model_name = model_cfg if model_cfg != "auto" else (GPU_MODEL if use_cuda else CPU_MODEL)

        self.log(f"   准备转录模型 {model_name}（第一次使用会自动下载）…")
        try:
            local_path = download_model(model_name, cache_dir=str(self.cfg.models_dir))
        except Exception as e:  # noqa: BLE001
            raise AsrError(f"模型下载失败：{str(e).splitlines()[0][:120]}（可检查网络，或 config hf_mirror true）")

        if use_cuda:
            ok, err = gpu_check(self.cfg, local_path)
            if not ok:
                self.note = err
                if want == "cuda":
                    raise AsrError(f"显卡用不上：{err}")
                use_cuda = False
                if model_cfg == "auto":
                    model_name = CPU_MODEL
                    local_path = download_model(model_name, cache_dir=str(self.cfg.models_dir))
        self.device = "cuda" if use_cuda else "cpu"
        self.model_name = model_name
        if not use_cuda and self.note:
            self.log(f"   ⚠️ 显卡没用上：{self.note}。这次用 CPU，速度会慢很多。")
        self.model = WhisperModel(local_path, device=self.device,
                                  compute_type="float16" if use_cuda else "int8")

    def describe(self) -> str:
        return f"本地 {self.model_name}（{'显卡' if self.device == 'cuda' else 'CPU'}）"

    def transcribe(self, path: Path) -> Transcript:
        self.load()
        audio = decode(path)
        lang = str(self.cfg.get("language") or "auto")
        language = None if lang == "auto" else lang
        if language is None:
            try:  # 先识别语言，中文再加带标点的提示词
                language = self.model.detect_language(audio)[0]
            except Exception:  # noqa: BLE001  旧版 faster-whisper 没有这个方法
                language = None
        segments, info = self.model.transcribe(
            audio,
            language=language,
            initial_prompt=ZH_PROMPT if language in ("zh", "yue") else None,
            vad_filter=True,
            beam_size=5,
            condition_on_previous_text=False,
        )
        segs = [(float(s.start), float(s.end), s.text) for s in segments]
        language = getattr(info, "language", "") or ""
        paras = group_segments(segs)
        if language in ("zh", "yue"):
            paras = [(st, to_simplified(t)) for st, t in paras]
        return Transcript(paragraphs=paras, language=language,
                          duration=float(getattr(info, "duration", 0) or 0))


class SiliconFlow:
    name = "cloud"

    def __init__(self, cfg: Config, log: Callable[[str], None] = print):
        self.cfg = cfg
        self.log = log
        self.key = str(cfg.get("siliconflow_api_key") or "").strip()
        if not self.key:
            raise AsrError("云端转录需要硅基流动的 API Key → 运行：dyknow config siliconflow_api_key 你的Key")

    def describe(self) -> str:
        return "云端 SenseVoice（硅基流动）"

    def _post(self, wav_path: Path) -> str:
        import requests

        with open(wav_path, "rb") as f:
            r = requests.post(SILICONFLOW_URL, headers={"Authorization": f"Bearer {self.key}"},
                              files={"file": (wav_path.name, f, "audio/wav")},
                              data={"model": SILICONFLOW_MODEL}, timeout=300)
        if r.status_code == 401:
            raise AsrError("硅基流动 API Key 无效")
        if r.status_code >= 400:
            raise AsrError(f"云端转录失败：HTTP {r.status_code} {r.text[:80]}")
        text = str(r.json().get("text") or "")
        return re.sub(r"<\|[^|]*\|>", "", text).strip()

    def transcribe(self, path: Path) -> Transcript:
        audio = decode(path)
        total = len(audio) / 16000
        step = CLOUD_CHUNK_SECONDS * 16000
        paras: list[tuple[float | None, str]] = []
        for i in range(0, max(len(audio), 1), step):
            chunk = audio[i:i + step]
            if len(chunk) < 16000 * 0.5:
                continue
            wav = path.with_name(f"{path.stem}_{i // step}.wav")
            _write_wav(wav, chunk)
            try:
                text = self._post(wav)
            finally:
                wav.unlink(missing_ok=True)
            paras += split_plain(to_simplified(text), offset=i / 16000)
        return Transcript(paragraphs=paras, language="", duration=total)


def _write_wav(path: Path, samples) -> None:
    import numpy as np

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)


def make(cfg: Config, log: Callable[[str], None] = print):
    """按配置选择转录方式。返回 None 表示不转录"""
    mode = str(cfg.get("asr") or "auto")
    if mode == "none":
        return None
    if mode == "cloud":
        return SiliconFlow(cfg, log)
    if mode == "local":
        return LocalWhisper(cfg, log)
    # auto：装了 faster-whisper 且（有显卡 或 没配云端 Key）→ 本地；否则云端
    has_local = importlib.util.find_spec("faster_whisper") is not None
    has_key = bool(str(cfg.get("siliconflow_api_key") or "").strip())
    if has_local and (cuda_device_count() > 0 or not has_key):
        return LocalWhisper(cfg, log)
    if has_key:
        return SiliconFlow(cfg, log)
    raise AsrError("没有可用的转录方式 → 运行：pip install faster-whisper，或配置硅基流动 Key")
