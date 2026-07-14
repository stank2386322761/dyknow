#!/usr/bin/env python3
"""
自动生成知识库索引 MD v7 — 对齐版本1 格式
- 标题去 hashtags，要点限3条
- 格式: ### 🎬 Title / - **作者**: / - **一句话**: / - **要点**: / - **金句**:
- 仅输出已处理条目，无文件链接
"""
import sys
import re
import yaml
from pathlib import Path
from datetime import datetime
from collections import defaultdict

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = Path(__file__).resolve().parent.parent
OUTPUT = BASE / "抖音收藏"
ROOT = OUTPUT / "抖音收藏知识库"

ICONS = {
    "AI工具与开发": "🤖", "AI视频创作": "🎬",
    "自媒体与运营": "📱", "商业与投资": "💰",
    "思维与成长": "🧠", "生活与兴趣": "🌈",
}


def get_icon(name: str) -> str:
    for k, v in ICONS.items():
        if k in name:
            return v
    return "📂"


def clean_title(t: str) -> str:
    """去 hashtags、去冗余前缀、截断"""
    # 去掉末尾 #tag #tag ...
    t = re.sub(r'\s*#\S+(?:\s+#\S+)*\s*$', '', t)
    # 去掉前面 #tag
    t = re.sub(r'^\s*#\S+(?:\s+#\S+)*\s*', '', t)
    # 去掉「...」前缀
    t = re.sub(r'^[「「].+?[」」]\s*', '', t)
    return t.strip()[:80]


def trim_summary(s: str) -> str:
    """精简一句话：去冗余开头"""
    s = re.sub(
        r'^(作者|博主|视频|本期|今天|这期)?\s*(分享了?|介绍了?|讲解了?|揭露了?'
        r'|分析了?|总结了?|推荐了?|展示了?|手把手|保姆级)\s*[：:]\s*', '', s
    )
    return s.strip()


def _clean_line(l: str) -> str:
    """清理要点行"""
    l = re.sub(r'^\d{1,2}:\d{2}\s*[｜|\-—]\s*', '', l)
    l = re.sub(r'^\d+\.\s*', '', l)
    l = re.sub(r'\*\*', '', l)
    return l.strip().lstrip('*- ').strip()


def parse_note(filepath: Path) -> dict:
    """从 MD 文件提取元数据和摘要"""
    info = {
        "title": "", "author": "", "likes": 0,
        "summary": "", "points": "", "quote": "",
        "status": "indexed",
    }
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return info

    fm_match = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
    body = text
    if fm_match:
        body = text[fm_match.end():]
        try:
            fm = yaml.safe_load(fm_match.group(1))
            if fm:
                info["title"] = clean_title(str(fm.get("title", "")))
                info["author"] = str(fm.get("author", ""))
                info["likes"] = int(fm.get("likes", 0) or 0)
                info["status"] = str(fm.get("status", "indexed"))
        except Exception:
            pass

    # ── 一句话 ──
    m = re.search(r'##\s*📌\s*概述\s*\n(.*?)(?=\n##|\Z)', body, re.DOTALL)
    if m:
        quotes = re.findall(r'^>\s*(.+)', m.group(1), re.MULTILINE)
        real = [q.strip() for q in quotes if q.strip()
                and not any(kw in q for kw in ['待模型', '待总结', '待转录'])]
        if real:
            info["summary"] = trim_summary(real[0][:200])

    if not info["summary"]:
        m = re.search(r'##\s*🤖\s*AI\s*摘要.*?\n(.*?)(?=\n##|\Z)', body, re.DOTALL)
        if m:
            s = re.search(r'(?:\*\*一句话总结[：:]\*\*|###\s*一句话总结)\s*(.+)', m.group(1))
            if s:
                info["summary"] = trim_summary(s.group(1).strip()[:200])

    # ── 要点（最多3条，不截断每条内容）──
    m = re.search(r'##\s*🎬\s*章节要点\s*\n(.*?)(?=\n##|\Z)', body, re.DOTALL)
    if m:
        raw = m.group(1).strip().split('\n')
        cleaned = []
        for l in raw:
            l = _clean_line(l)
            if l and not any(kw in l for kw in ['待转录', '待生成', '_待']):
                cleaned.append(l)
        if cleaned:
            # 取前3条，不截断每条内容
            info["points"] = " / ".join(cleaned[:3])

    # ── 金句 ──
    m = re.search(r'##\s*💡\s*金句.*?\n(.*?)(?=\n##|\Z)', body, re.DOTALL)
    if m:
        section = m.group(1).strip()
        quotes = re.findall(r'"(.+?)"', section)
        if not quotes:
            quotes = re.findall(r'^>\s*(.+)', section, re.MULTILINE)
        if not quotes:
            raw = re.findall(r'^[-*\d]+\.?\s*(.+)', section, re.MULTILINE)
            quotes = [q.strip().strip('"').strip() for q in raw]
        real = [q.strip() for q in quotes if q.strip()
                and len(q.strip()) >= 10
                and not any(kw in q for kw in ['待总结', '待填入', '_待'])]
        if real:
            info["quote"] = real[0][:200]

    return info


def build_index() -> str:
    today = datetime.now().strftime("%Y-%m-%d")

    categories = defaultdict(list)
    total = 0
    done_count = 0

    if not ROOT.exists():
        return f"# 错误：知识库目录不存在\n\n> {ROOT}"

    for cat_dir in sorted(ROOT.iterdir()):
        if not cat_dir.is_dir():
            continue
        for sub_dir in sorted(cat_dir.iterdir()):
            if not sub_dir.is_dir():
                continue
            for md_file in sorted(sub_dir.glob("*.md")):
                total += 1
                info = parse_note(md_file)
                is_done = bool(info["summary"]) or info["status"] in ("done", "transcribed")
                if is_done:
                    done_count += 1
                categories[cat_dir.name].append((md_file, info, is_done))

    # ── 生成 ──
    lines = []
    lines.append("---")
    lines.append("title: 抖音收藏知识库索引")
    lines.append(f"date: {today}")
    lines.append(f"total: {done_count}")
    lines.append("---")
    lines.append("")
    lines.append("# 📚 抖音收藏知识库")
    lines.append("")
    lines.append(f"> 最后更新：{today} | 已处理：{done_count} 条")
    lines.append("")

    # 分类统计 —— 无 emoji，去数字前缀
    lines.append("## 📊 分类统计")
    lines.append("")
    lines.append("| 分类 | 数量 |")
    lines.append("|------|------|")
    for cat_name in sorted(categories.keys()):
        cat_done = sum(1 for _, _, d in categories[cat_name] if d)
        if cat_done:
            display = re.sub(r'^\d+-', '', cat_name)
            lines.append(f"| {display} | {cat_done} |")
    lines.append("")

    # 按大类
    for cat_name in sorted(categories.keys()):
        entries = categories[cat_name]
        done_entries = [(f, i) for f, i, d in entries if d]
        if not done_entries:
            continue

        icon = get_icon(cat_name)
        display = re.sub(r'^\d+-', '', cat_name)
        lines.append("---")
        lines.append("")
        lines.append(f"## {icon} {display}（{len(done_entries)}条）")
        lines.append("")

        for md_file, info in done_entries:
            title = info["title"] or md_file.stem
            lines.append(f"### 🎬 {title}")
            if info["author"]:
                lk = f" | 👍 {info['likes']:,}" if info["likes"] else ""
                lines.append(f"- **作者**: {info['author']}{lk}")
            if info["summary"]:
                lines.append(f"- **一句话**: {info['summary']}")
            if info["points"]:
                lines.append(f"- **要点**: {info['points']}")
            if info["quote"]:
                lines.append(f"- **金句**: \"{info['quote']}\"")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"> 📌 本知识库通过抖音收藏笔记自动整理生成 | 最后更新: {today}")

    return "\n".join(lines)


def main():
    content = build_index()
    index_path = OUTPUT / "知识库索引.md"
    index_path.write_text(content, encoding="utf-8")
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"Index updated: {index_path}")
    fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if fm_match:
        fm = yaml.safe_load(fm_match.group(1))
        print(f"   {fm.get('total', '?')} entries | {today}")


if __name__ == "__main__":
    main()
