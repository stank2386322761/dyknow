#!/usr/bin/env python3
"""
DyKnow 笔记统一归档 v3
- 所有笔记归入 抖音收藏知识库/
- 内部按 6大类 → 26个子类 细分
"""
import shutil, sqlite3, re, sys
from pathlib import Path
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent.parent
OUTPUT = BASE / "抖音收藏"
ROOT = OUTPUT / "抖音收藏知识库"
DB_PATH = BASE / "data" / "dyknow.db"

# ── 分类规则：子类 → 正则列表 ──
# 格式: ("大类/子类", [正则...])
# 匹配优先级从上到下，命中即停止
RULES = [
    # ============ 01-AI工具与开发 ============
    ("01-AI工具与开发/01-Codex与ClaudeCode", [
        r"codex", r"claude.*code", r"claude.*配", r"claude.*接",
        r"vibe.*cod", r"vibe.*编程", r"cursor", r"windsurf", r"copilot",
    ]),
    ("01-AI工具与开发/02-ComfyUI与AI绘画", [
        r"comfyui", r"stable.*diffusion", r"midjourney", r"dalle",
        r"AI.*绘画", r"AI.*画图", r"AI.*生图", r"AI.*绘图",
        r"lora.*模型", r"controlnet",
    ]),
    ("01-AI工具与开发/03-大模型与部署", [
        r"大模型.*部署", r"大模型.*安装", r"大模型.*本地", r"本地.*部署.*模型",
        r"qwen", r"llama", r"deepseek", r"grok", r"gemini",
        r"openai.*科学家", r"openai.*研究员",
        r"whisper", r"asr.*模型", r"tts.*模型", r"语音.*克隆", r"声音克隆",
        r"AI.*装机", r"AI.*主机", r"AI.*硬件", r"显卡.*AI",
    ]),
    ("01-AI工具与开发/04-Agent与工作流", [
        r"agent.*开发", r"agent.*搭建", r"agent.*部署", r"agent.*框架",
        r"mcp", r"dify", r"coze", r"扣子", r"n8n",
        r"AI.*工作流", r"工作流.*AI", r"AI.*自动化", r"自动化.*AI",
        r"rag", r"langchain", r"向量", r"embedding",
        r"prompt.*engineer", r"提示词.*工程",
    ]),
    ("01-AI工具与开发/05-开源项目与开发", [
        r"github", r"开源.*项目", r"开源.*工具", r"开源.*引擎",
        r"编程", r"程序员", r"python.*AI", r"AI.*python",
        r"代码.*生成", r"代码.*写", r"开发.*工具",
        r"API.*调用", r"firecrawl",
        r"AI.*搜索", r"AI.*效率", r"AI.*阅读", r"AI.*总结",
        r"AI.*工具.*推荐", r"AI.*神器",
        r"skill.*开源", r"skill.*开发", r"skill.*制作", r"skill.*底层",
    ]),

    # ============ 02-AI视频创作 ============
    ("02-AI视频创作/01-视频生成工具", [
        r"seedance", r"sora", r"veo", r"kling", r"可灵", r"runway", r"pika",
        r"即梦", r"豆包.*视频", r"视频.*豆包", r"wan.*视频",
        r"视频.*生成", r"生成.*视频", r"视频.*模型",
        r"hyperframe", r"hyper.*frame",
        r"luma", r"小云雀",
    ]),
    ("02-AI视频创作/02-AI漫剧与动画", [
        r"AI.*漫剧", r"漫剧.*AI", r"AI.*动画", r"AI.*动漫",
        r"AI.*短片", r"AI.*微电影", r"AI.*影视",
        r"皮克斯.*动画", r"动画.*AI",
        r"AI.*导演", r"AI.*制片", r"AI.*电影",
    ]),
    ("02-AI视频创作/03-口播与数字人", [
        r"口播", r"数字人", r"AI.*主播",
        r"不露脸.*视频", r"不露脸.*赛道", r"不露脸.*博主",
    ]),
    ("02-AI视频创作/04-剪辑与后期", [
        r"AI.*剪辑", r"剪辑.*AI", r"剪映",
        r"AI.*配音", r"配音.*AI", r"AI.*字幕",
        r"AI.*特效", r"特效.*AI",
        r"codex.*视频", r"codex.*剪辑", r"codex.*剪",
        r"AI.*视频.*教程", r"视频.*制作", r"视频.*创作",
    ]),
    ("02-AI视频创作/05-带货与商用视频", [
        r"AI.*带货.*视频", r"视频.*带货.*AI",
        r"AI.*换装", r"AI.*时装", r"AI.*走秀", r"AI.*服饰",
        r"电商.*视频", r"视频.*电商",
        r"AI.*宠物.*视频", r"宠物.*带货",
        r"skill.*带货", r"AI.*街拍",
    ]),

    # ============ 03-自媒体与运营 ============
    ("03-自媒体与运营/01-起号与涨粉", [
        r"起号", r"涨粉", r"冷启动", r"从0到1",
        r"万粉.*博主", r"博主.*收入",
    ]),
    ("03-自媒体与运营/02-选题与文案", [
        r"选题", r"文案", r"脚本.*写作", r"写作.*脚本",
        r"口播.*稿", r"标题.*技巧", r"标题.*写法",
    ]),
    ("03-自媒体与运营/03-平台玩法与算法", [
        r"自媒体", r"新媒体",
        r"推流", r"算法.*升级", r"流量.*密码", r"流量.*机制",
        r"小红书", r"视频号", r"b站", r"快手",
        r"抖音.*运营", r"短视频.*运营",
        r"平台.*规则", r"去标签",
        r"traework", r"trae.*work",
    ]),
    ("03-自媒体与运营/04-变现与IP打造", [
        r"个人IP", r"IP.*打造",
        r"变现", r"知识付费", r"私域",
        r"矩阵.*号", r"切片",
        r"短视频.*带货", r"图文.*带货",
        r"MCN", r"达人",
        r"新媒体.*赚钱", r"自媒体.*赚钱",
    ]),

    # ============ 04-商业与投资 ============
    ("04-商业与投资/01-创业与副业", [
        r"创业", r"副业", r"裸辞",
        r"一人.*公司", r"solo.*founder", r"超级个体",
        r"商业模式", r"生意", r"项目.*赚钱",
        r"网赚", r"兼职",
        r"信息差.*赚", r"赚.*信息差",
        r"独立开发者", r"自由职业",
    ]),
    ("04-商业与投资/02-股票与量化", [
        r"股票", r"基金", r"理财",
        r"量化交易", r"量化.*投资", r"量化.*引擎",
        r"A股", r"港股", r"美股", r"沪深", r"期货", r"外汇",
        r"交易.*系统", r"交易.*策略", r"交易.*思维", r"交易员",
        r"巴菲特", r"价值投资", r"基本面",
        r"Vibe.*Trading", r"AI.*炒股", r"AI.*选股",
        r"投研", r"投资.*框架", r"投资.*方法论",
    ]),
    ("04-商业与投资/03-赚钱思维与认知", [
        r"赚钱.*思维", r"搞钱.*思维", r"搞钱$", r"赚钱$",
        r"财富.*认知", r"富人.*思维", r"财商",
        r"被动收入", r"睡后收入",
        r"赚钱.*逻辑", r"赚钱.*方法",
        r"暴利.*生意", r"暴利.*赛道",
        r"穷人.*思维", r"思维.*赚钱",
    ]),
    ("04-商业与投资/04-市场分析与报告", [
        r"资本.*布局", r"资本.*赛道", r"资本.*市场",
        r"财报", r"上市公司", r"产业链",
        r"金融", r"经济", r"市场.*分析",
        r"胡润", r"财富.*报告",
        r"数字货币", r"比特币", r"币圈", r"web3", r"nft",
        r"GEO", r"生成式.*引擎", r"搜索引擎.*优化",
        r"行业.*趋势", r"赛道.*分析",
    ]),

    # ============ 05-思维与成长 ============
    ("05-思维与成长/01-认知与思维", [
        r"认知", r"思维.*升级", r"底层逻辑", r"思维.*方式",
        r"格局", r"破局", r"觉醒",
        r"强者.*思维", r"弱者.*思维",
    ]),
    ("05-思维与成长/02-学习方法与效率", [
        r"学习.*方法", r"学习.*法", r"学习.*效率",
        r"专注力", r"拖延症", r"自律", r"习惯.*养成",
        r"时间管理", r"效率.*提升",
        r"读书.*方法", r"阅读.*方法",
    ]),
    ("05-思维与成长/03-毛选与哲学", [
        r"毛选", r"教员", r"矛盾论", r"实践论",
        r"道德经", r"老子", r"国学", r"天道",
        r"哲学", r"人生.*智慧",
    ]),
    ("05-思维与成长/04-职业与人生规划", [
        r"职业.*规划", r"职业.*方向", r"求职",
        r"人生.*建议", r"人生.*道理", r"人生.*规划",
        r"20.*30.*岁", r"给.*岁.*建议",
        r"身弱", r"高敏", r"内耗",
        r"女性.*成长", r"自我.*提升",
    ]),

    # ============ 06-生活与兴趣 ============
    ("06-生活与兴趣/01-音乐与影视", [
        r"音乐", r"MV", r"演唱会", r"福音", r"说唱", r"唱歌",
        r"拉片", r"视听语言", r"电影.*解析",
        r"华强买瓜", r"玩具总动员",
    ]),
    ("06-生活与兴趣/02-体育与赛事", [
        r"世界杯", r"足球", r"NBA", r"体育",
    ]),
    ("06-生活与兴趣/03-情感与关系", [
        r"情感", r"亲密关系", r"恋爱", r"婚姻",
        r"女性.*独立", r"女性.*力量",
        r"girlstalk",
    ]),
    ("06-生活与兴趣/04-日常与娱乐", [
        r"猫咪", r"猫猫", r"宠物",
        r"日常", r"vlog", r"生活",
        r"搞笑", r"娱乐", r"趣事",
    ]),
]


def classify(title: str) -> str:
    for subfolder, patterns in RULES:
        for pat in patterns:
            if re.search(pat, title, re.IGNORECASE):
                return subfolder
    return "06-生活与兴趣/04-日常与娱乐"  # 兜底


def resolve_path(stored_path: str | None, aweme_id: str) -> Path | None:
    if stored_path:
        p = Path(stored_path)
        if p.exists():
            return p
    suffix = f"_{aweme_id}.md"
    try:
        for md in OUTPUT.rglob(f"*{suffix}"):
            return md
    except Exception:
        pass
    return None


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM sync_log").fetchall()
    print(f"总计: {len(rows)} 条")

    # ── 预分类 ──
    pred = defaultdict(int)
    for r in rows:
        pred[classify(r["title"])] += 1
    print("\n=== 分类预测 ===")
    for f, cnt in sorted(pred.items()):
        print(f"  {f}: {cnt}")

    # ── 执行移动 ──
    print("\n=== 执行归档 ===")
    moved = defaultdict(int)
    not_found = 0

    for r in rows:
        old_path = resolve_path(r["note_path"], r["aweme_id"])
        if not old_path:
            not_found += 1
            continue

        sub = classify(r["title"])
        new_dir = ROOT / sub
        new_dir.mkdir(parents=True, exist_ok=True)
        new_path = new_dir / old_path.name

        try:
            if old_path != new_path:
                if new_path.exists():
                    old_path.unlink(missing_ok=True)
                else:
                    shutil.move(str(old_path), str(new_path))
                moved[sub] += 1

            if str(new_path) != r["note_path"]:
                conn.execute(
                    "UPDATE sync_log SET note_path = ? WHERE aweme_id = ?",
                    (str(new_path), r["aweme_id"]),
                )
        except Exception as e:
            print(f"  错误 [{r['aweme_id'][:8]}]: {e}")

    conn.commit()

    # ── 清理旧空目录 ──
    old_dirs = {"AI工具与开发", "AI视频创作", "投资与商业", "自媒体运营", "未分类"}
    for d in sorted(OUTPUT.iterdir()):
        if d.is_dir() and d.name in old_dirs:
            try:
                d.rmdir()
                print(f"  清理旧目录: {d.name}/")
            except OSError:
                # 非空，强行递归删
                for f in d.rglob("*"):
                    f.unlink(missing_ok=True)
                for sd in sorted(d.rglob("*"), reverse=True):
                    if sd.is_dir():
                        sd.rmdir()
                d.rmdir()
                print(f"  清理旧目录(强): {d.name}/")

    conn.commit()

    # ── 报告 ──
    print(f"\n✅ 共移动 {sum(moved.values())} 篇")
    print("\n=== 最终分布 ===")
    for d in sorted(ROOT.iterdir()):
        if d.is_dir():
            mds = list(d.rglob("*.md"))
            total_kb = sum(m.stat().st_size for m in mds) / 1024
            print(f"\n  {d.name}/  ({len(mds)} 篇, {total_kb:.0f} KB)")
            for sd in sorted(d.iterdir()):
                if sd.is_dir():
                    smds = list(sd.glob("*.md"))
                    print(f"    {sd.name}/  {len(smds)} 篇")

    if not_found:
        print(f"\n⚠️  文件未找到: {not_found} 条")

    conn.close()
    print(f"\n🎉 归档完成！目录: {ROOT}")


if __name__ == "__main__":
    main()
