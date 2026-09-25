"""
Markdown frontmatter 读写

设计原则（v2.1 修订，修复数据丢失风险）
────────────────────────────────────────
**读**：优先使用 PyYAML 可靠解析（支持多行列表、嵌套结构、各种类型）。
        PyYAML 不可用或该笔记的 frontmatter 不是合法 YAML 时，
        回退到内置的简单解析器（flat scalar + 单行 list）。

**写**：只逐行替换目标字段，**其余原始行原样保留**。
        即 update_meta / set_field 不会重新序列化整个 frontmatter，
        因此以下内容永远不会因为更新 status / category / hash 而被删除：

          - 多行 YAML 列表（块状 tags 等）
          - 嵌套映射
          - 未知的自定义字段
          - 用户手工添加的注释与格式

        （不保证排版逐字不变，但保证数据不丢、语义不变。）

只有生成**全新笔记**时才使用 dump_frontmatter 做完整序列化。
"""

from __future__ import annotations

import re
from pathlib import Path

try:  # pragma: no cover - 取决于环境
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# 匹配文件开头的 frontmatter 块
_FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)

_QUOTE_CHARS = ('"', "'")

# 写入时若不加引号会被 YAML 解析成非字符串的值
_AMBIGUOUS = {
    "true", "false", "null", "~", "yes", "no", "on", "off", "none",
}

_INT_RE = re.compile(r"-?\d+")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


# ── 解析 ──────────────────────────────────────


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    解析笔记文本，返回 (meta, body)。

    - 无 frontmatter 时返回 ({}, 原文)
    - 使用 PyYAML 时值类型更准确（list / int / date 等）
    - YAML 非法时回退到简单解析，绝不抛异常
    """
    if not text:
        return {}, ""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    return _load_block(m.group(1)), text[m.end():]


def _load_block(block: str) -> dict:
    """解析 frontmatter 块为 dict"""
    if yaml is not None:
        try:
            data = yaml.safe_load(block)
        except Exception:
            data = None
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items()}
        if data is None:
            # 空块或纯注释块
            if not block.strip() or all(
                ln.strip().startswith("#") for ln in block.splitlines() if ln.strip()
            ):
                return {}
        # 解析结果不是映射（例如整块被解析成字符串）→ 回退
    return _parse_block_simple(block)


def _coerce(raw: str):
    """把 YAML 标量文本转成 Python 值（字符串 / 整数 / 列表）"""
    v = raw.strip()
    if not v:
        return ""
    if len(v) >= 2 and v[0] == v[-1] and v[0] in _QUOTE_CHARS:
        inner = v[1:-1]
        if v[0] == '"':
            inner = inner.replace('\\"', '"')
        return inner
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(x.strip()) for x in inner.split(",") if x.strip()]
    if _INT_RE.fullmatch(v):
        return int(v)
    return v


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in _QUOTE_CHARS:
        return s[1:-1]
    return s


def _parse_block_simple(block: str) -> dict:
    """
    内置简单解析器（PyYAML 缺失或解析失败时的回退）。

    只处理 flat scalar 与单行 flow list；多行块状列表的值会读成空串，
    但**不会**影响写回（写回走逐行替换，不依赖本函数的解析结果）。
    """
    meta: dict = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[:1] in (" ", "\t"):
            # 续行：属于上一个字段，简单解析器忽略
            continue
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        if not key:
            continue
        meta[key] = _coerce(raw)
    return meta


# ── 序列化（仅用于生成全新笔记） ───────────────


def _needs_quote(s: str) -> bool:
    if s == "":
        return True
    if s != s.strip():
        return True
    if s[0] in _QUOTE_CHARS:
        return True
    if s[0] in "[{#&*!|>%@`":
        return True
    if ":" in s or " #" in s:
        return True
    if s.lower() in _AMBIGUOUS:
        return True
    if _NUM_RE.fullmatch(s):
        return True
    return False


def dump_value(v) -> str:
    """把一个 Python 值序列化为 frontmatter 右侧文本"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(dump_value(x) for x in v) + "]"
    if isinstance(v, int):
        return str(v)
    s = "" if v is None else str(v)
    if _needs_quote(s):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def dump_frontmatter(meta: dict, body: str = "") -> str:
    """把 meta + body 组装成完整笔记文本（meta 为空时不输出 frontmatter）"""
    if not meta:
        return body
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {dump_value(v)}")
    lines.append("---")
    head = "\n".join(lines)
    if not body:
        return head + "\n"
    return head + "\n" + body


# ── 文件级读写 ────────────────────────────────


def read_note(path: Path) -> tuple[dict, str]:
    """读取笔记文件，返回 (meta, body)。文件不存在或读取失败返回 ({}, "")"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}, ""
    return parse_frontmatter(text)


def read_text(path: Path) -> str:
    """读取笔记原文（失败返回空串）"""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def write_note(path: Path, meta: dict, body: str) -> None:
    """原子写入完整笔记（用于生成新笔记）"""
    atomic_write_text(Path(path), dump_frontmatter(meta, body))


def update_meta(path: Path, updates: dict, body_updates: dict | None = None) -> bool:
    """
    就地更新 frontmatter 中的**指定字段**，其余内容原样保留。

    与 v2 早期实现的关键区别：不再 `merged = dict(meta); merged.update(...)`
    后整体重写——那样的实现会让简单解析器读不到的行（多行列表、嵌套映射、
    自定义字段）在写回时被静默删除。

    参数:
        updates:      {字段名: 新值}，只改这些字段
        body_updates: {旧正文片段: 新正文片段}

    返回:
        是否发生了写入
    """
    path = Path(path)
    if not path.exists():
        return False

    text = read_text(path)
    if not text:
        return False

    new_text = text
    for key, value in (updates or {}).items():
        new_text = set_field(new_text, key, value)

    if body_updates:
        for old, new in body_updates.items():
            if old in new_text:
                new_text = new_text.replace(old, new)

    if new_text == text:
        return False

    atomic_write_text(path, new_text)
    return True


def set_field(text: str, key: str, value) -> str:
    """
    在 frontmatter 中设置单个字段，**逐行替换**，其余内容原样保留。

    若该字段当前是多行 YAML 值（块状列表 / 嵌套映射），
    连同它的缩进续行一起替换，避免留下孤立的 `- item` 行导致 YAML 非法。
    字段不存在时追加到 frontmatter 末尾。
    """
    m = _FM_RE.match(text or "")
    if not m:
        return text

    lines = m.group(1).split("\n")
    rendered = f"{key}: {dump_value(value)}"
    out: list[str] = []
    replaced = False
    i = 0
    total = len(lines)

    while i < total:
        line = lines[i]
        if not replaced and _is_field_line(line, key):
            out.append(rendered)
            replaced = True
            i += 1
            # 跳过该字段的缩进续行（块状列表 / 嵌套映射）
            while i < total and _is_continuation(lines[i]):
                i += 1
            continue
        out.append(line)
        i += 1

    if not replaced:
        out.append(rendered)

    return text[: m.start(1)] + "\n".join(out) + text[m.end(1):]


def _is_field_line(line: str, key: str) -> bool:
    """判断某行是否是顶层 `key:` 字段行（顶层 = 无缩进）"""
    if not line or line[:1] in (" ", "\t"):
        return False
    k, sep, _v = line.partition(":")
    return bool(sep) and k.strip() == key


def _is_continuation(line: str) -> bool:
    """判断某行是否是上一个字段的缩进续行"""
    if not line.strip():
        return False
    return line[:1] in (" ", "\t")


# ── 正文区块 ──────────────────────────────────


_SECTION_CACHE: dict[str, re.Pattern] = {}


def _section_re(heading: str) -> re.Pattern:
    """构造匹配 `## <heading>` 到下一个 `## ` 之间的正则"""
    pat = _SECTION_CACHE.get(heading)
    if pat is None:
        esc = re.escape(heading)
        pat = re.compile(
            rf"^##\s*{esc}\s*\r?\n(.*?)(?=^##\s|\Z)",
            re.DOTALL | re.MULTILINE,
        )
        _SECTION_CACHE[heading] = pat
    return pat


def extract_section(body: str, heading: str) -> str:
    """提取正文中某个二级标题下的内容（不含标题本身），找不到返回空串"""
    m = _section_re(heading).search(body or "")
    return m.group(1).strip() if m else ""


def replace_section(body: str, heading: str, content: str) -> str:
    """
    替换或追加二级标题区块。找不到该标题时追加到正文末尾。

    替换后自动补一个 `---` 分隔线，与笔记正文的既有排版保持一致。
    """
    pat = _section_re(heading)
    m = pat.search(body or "")
    new_block = f"## {heading}\n\n{content.strip()}\n\n---\n"
    if m:
        start = m.start()
        end = m.end()
        return body[:start] + new_block + "\n" + body[end:].lstrip("\n")
    body = (body or "").rstrip()
    return body + f"\n\n---\n\n{new_block}"


def atomic_write_text(path: Path, content: str) -> None:
    """原子写入（委托 generator，保持单一实现）"""
    from .generator import atomic_write_text as _aw

    _aw(Path(path), content)
