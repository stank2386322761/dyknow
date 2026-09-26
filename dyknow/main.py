"""
CLI 入口 —— DyKnow
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

from .config import config
from .db import get_db


def cmd_login(_args):
    from .login import login as do_login
    cookies = asyncio.run(do_login())
    return 0 if cookies else 1


def cmd_status(_args):
    db = get_db()
    from .login import load_cookies

    print("\nDyKnow 状态")
    print("=" * 50)
    print(f"登录: {'已登录' if load_cookies() else '未登录'}")
    print(f"笔记目录: {config.output_dir}")
    print(f"           （来源：{config.output_dir_source()}）")
    print(f"数据库:   {config.db_path}")
    print()
    print("转录流水线状态:")
    for status_name, cnt in sorted(db.get_status_breakdown().items()):
        print(f"  {status_name:<22} {cnt:>5}")
    print(f"  {'总计':<22} {db.total_count():>5}")

    breakdown = db.get_processing_breakdown()
    if breakdown:
        print()
        print("AI 知识处理状态:")
        for name in ("pending", "done", "needs_review"):
            print(f"  {name:<22} {breakdown.get(name, 0):>5}")
    db.close()
    return 0


def cmd_sync(args):
    from .syncer import Syncer
    _apply_output(args)
    return Syncer().sync(
        count=args.count,
        only_index=args.only_index,
        only_transcribe=args.only_transcribe,
        transcribe=args.transcribe,
        full=args.full,
    )


def cmd_transcribe(args):
    from .syncer import Syncer
    _apply_output(args)
    return Syncer()._phase2_transcribe(
        count=args.count,
        include_failed=args.retry_failed,
        max_attempts=args.max_attempts,
    )


def _parse_ids_arg(raw: str) -> list[str]:
    """
    解析 reset --ids 的取值，返回 aweme_id 列表。

    取值可以是逗号/空白分隔的 id 串，也可以是一个文件路径
    （每行一个 id，允许 # 注释）。有了它，子集重跑不必再手工改数据库。
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    p = Path(raw)
    if p.is_file():
        items = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                items.append(line)
        return items
    return [x for x in re.split(r"[,\s]+", raw) if x]


def cmd_reset(args):
    """
    把指定任务回退到 pending_index（强制重跑）。
    默认操作：把所有 status=transcribed 重置（重新转录）。
    指定 --ids 时只重置这些 id，忽略 --target。
    """
    db = get_db()
    from .db import (
        STATUS_PENDING_INDEX, STATUS_TRANSCRIBED,
        STATUS_FAILED, TRANSCRIBE_PIPELINE_STATUSES,
    )

    target = args.target
    ids = _parse_ids_arg(getattr(args, "ids", "") or "")

    if ids:
        placeholders = ",".join("?" * len(ids))
        existing = {
            r["aweme_id"]
            for r in db.conn.execute(
                f"SELECT aweme_id FROM sync_log WHERE aweme_id IN ({placeholders})",
                tuple(ids),
            ).fetchall()
        }
        missing = [i for i in ids if i not in existing]
        if missing:
            print(f"⚠️  数据库中不存在、已忽略 {len(missing)} 个 id: {', '.join(missing[:10])}")
        rows = [{"aweme_id": i} for i in ids if i in existing]
        scope = f"指定的 {len(rows)} 条"
    elif target == "transcribed":
        rows = db.get_by_status(STATUS_TRANSCRIBED)
        scope = f"{target} 状态的 {len(rows)} 条"
    elif target == "failed":
        rows = db.get_by_status(STATUS_FAILED)
        scope = f"{target} 状态的 {len(rows)} 条"
    elif target == "all":
        placeholders = ",".join("?" * len(TRANSCRIBE_PIPELINE_STATUSES))
        rows = db.conn.execute(
            f"SELECT aweme_id, status FROM sync_log WHERE status IN ({placeholders})",
            tuple(TRANSCRIBE_PIPELINE_STATUSES),
        ).fetchall()
        rows = [dict(r) for r in rows]
        scope = f"{target} 状态的 {len(rows)} 条"
    else:
        print(f"未知 target: {target}")
        return 1

    if not rows:
        print("没有匹配的任务")
        return 0

    if not args.yes:
        ans = input(f"将重置 {scope} 到 pending_index（同时清零 attempts），确认？(y/N): ")
        if ans.strip().lower() != "y":
            print("已取消")
            return 0

    for r in rows:
        db.reset_to_pending_index(r["aweme_id"])
    print(f"✅ 已重置 {len(rows)} 条到 pending_index（attempts 已清零）")
    return 0


def cmd_night(args):
    """夜间批量转录（时间窗口内循环运行）"""
    from .night_transcribe import main as night_main
    import sys
    # 模拟命令行参数
    sys.argv = [
        "night",
        "--start", args.start,
        "--end", args.end,
        "--max-attempts", str(args.max_attempts),
    ]
    return night_main()


def cmd_parse(args):
    from .parser import parse_and_generate
    output = Path(args.output) if args.output else None
    note_path = parse_and_generate(
        text=args.text,
        output_dir=output,
        transcribe_video=not args.no_transcribe,
    )
    if note_path:
        print(f"\nNOTE_PATH: {note_path}")
        return 0
    return 1


def cmd_browse(args):
    from .syncer import Syncer
    _apply_output(args)
    return Syncer().browse(
        keyword=args.keyword,
        count=args.count,
        transcribe=args.transcribe,
        full=args.full,
    )


def _apply_output(args) -> None:
    """
    把 `--output` 应用到全局配置（运行时覆盖，不写盘）。

    用于 sync / transcribe / browse 这类「根目录不是返回值」的命令。
    """
    raw = getattr(args, "output", None)
    if raw:
        config.output_dir = Path(raw).expanduser().resolve()


def _resolve_output(args) -> Path:
    """确定知识库根目录：命令行 --output 优先，其次配置（环境变量 → 配置文件 → 默认）"""
    raw = getattr(args, "output", None)
    if raw:
        return Path(raw).expanduser()
    return config.output_dir


def cmd_config(args):
    """
    查看 / 设置持久化配置。

    首次使用时用 `config --set-output <目录>` 记住笔记输出目录，
    之后每次运行都会自动读取，不必重复指定。
    """
    if args.set_output:
        target = config.save_output_dir(args.set_output)
        print("\n已保存笔记输出目录")
        print("=" * 50)
        print(f"  目录    : {target}")
        print(f"  配置文件: {config.config_path}")
        print("\n之后的运行都会使用该目录。")
        print("如需临时切换：加 --output <目录>，或设 DYKNOW_OUTPUT_DIR 环境变量。")
        return 0

    print("\nDyKnow 配置")
    print("=" * 50)
    print(f"  配置文件    : {config.config_path}")
    status = "已存在" if config.config_path.exists() else "尚未创建（执行 config --set-output 时生成）"
    print(f"  配置状态    : {status}")
    print(f"  数据目录    : {config.data_dir}")
    print(f"  笔记输出目录: {config.output_dir}")
    print(f"  目录来源    : {config.output_dir_source()}")
    print(f"  转录模型    : {config.ggml_model_path}")
    print(f"  保留媒体缓存: {config.keep_media_cache}")

    saved = config.read_config_file()
    if saved:
        print()
        print("  配置文件内容:")
        for k, v in saved.items():
            print(f"    {k}: {v}")

    print()
    print('  设置输出目录: python -m dyknow config --set-output "<目录>"')
    return 0


def _rel(path: Path, root: Path) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return str(path)


def cmd_index(args):
    """全量重建知识库索引（根索引 + 分类索引）"""
    from .indexer import rebuild_index

    root = _resolve_output(args)
    if not root.exists():
        print(f"知识库目录不存在: {root}")
        return 1

    result = rebuild_index(root, category_indexes=not args.no_category_index)
    st = result["stats"]

    print("\n索引已重建")
    print("=" * 50)
    for p in result["written"]:
        print(f"  ✅ {_rel(p, root)}")
    print("-" * 50)
    print(
        f"  已处理 {st['processed']} | 待处理 {st['pending']} "
        f"| 待聚类 {st['pending_cluster']} | 待复核 {st['needs_review']}"
    )
    if result["warnings"]:
        print("-" * 50)
        for w in result["warnings"][:20]:
            print(f"  ⚠️  {w}")
        if len(result["warnings"]) > 20:
            print(f"  ... 另有 {len(result['warnings']) - 20} 条提示")
    return 0


def cmd_organize(args):
    """按分类建议安全移动笔记（区分未处理 / 待聚类）"""
    from .organizer import Organizer

    root = _resolve_output(args)
    if not root.exists():
        print(f"知识库目录不存在: {root}")
        return 1

    org = Organizer(root, dry_run=args.dry_run, migrate=args.migrate)
    plans, _ = org.plan()
    result = org.apply(plans)

    mode = "[试用模式] " if args.dry_run else ""
    print(f"\n{mode}归档结果")
    print("=" * 50)
    if result.registered:
        print(f"  新注册分类: {', '.join(result.registered)}")

    verb = "计划移动" if args.dry_run else "已移动"
    print(f"  {verb}: {len(result.moved)}")
    for p in result.moved[:50]:
        print(f"     {_rel(p.src, root)}  →  {_rel(p.dst, root)}   （{p.reason}）")
    if len(result.moved) > 50:
        print(f"     ... 另有 {len(result.moved) - 50} 条")

    print(f"  ⏭️  跳过: {len(result.skipped)}")
    if result.cleaned_dirs:
        print(f"  🧹 清理旧空目录: {', '.join(result.cleaned_dirs)}")

    if result.conflicts:
        print(f"  ⚠️  内容冲突（两份均已保留，未自动合并）: {len(result.conflicts)}")
        for note in result.conflicts[:20]:
            print(f"     {note}")
        if len(result.conflicts) > 20:
            print(f"     ... 另有 {len(result.conflicts) - 20} 条")

    if result.failed:
        print(f"  ❌ 失败: {len(result.failed)}")
        for p, err in result.failed:
            print(f"     {_rel(p.src, root)}: {err}")

    if result.warnings:
        print("-" * 50)
        for w in result.warnings[:10]:
            print(f"  ⚠️  {w}")

    if args.dry_run:
        print("\n未修改任何文件。去掉 --dry-run 即按上述计划执行。")
    return 0


def cmd_taxonomy(args):
    """查看分类注册表"""
    from .taxonomy import Taxonomy

    root = _resolve_output(args)
    tax = Taxonomy(root).load()
    print(f"\n分类注册表: {tax.path}")

    if not tax.exists():
        print("  （尚未创建，运行 organize 后会在达到阈值时自动生成）")

    if args.paths_only:
        for p in tax.all_paths():
            print(p)
        return 0

    if not tax.categories:
        print("  （暂无分类）")
        return 0

    for top in tax.categories:
        alias = f"  别名: {', '.join(top.aliases)}" if top.aliases else ""
        print(f"  📂 {top.name}{alias}")
        for child in top.children:
            calias = f"  别名: {', '.join(child.aliases)}" if child.aliases else ""
            print(f"     └ {child.name}{calias}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="DyKnow — 抖音收藏自动化知识库工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
场景:
  A) 单视频    dyknow parse "<含抖音链接的文字>"
  B) 批量同步  dyknow sync
  C) 关键词    dyknow browse -k "关键词"
  D) 知识库    dyknow organize  →  dyknow index

示例:
  dyknow login                    扫码登录
  dyknow status                   查看状态
  dyknow parse "<分享文案>"        单视频处理
  dyknow sync                     增量同步收藏
  dyknow sync --transcribe        同步+转录
  dyknow transcribe               断点续转
  dyknow transcribe --retry-failed 仅重试失败项
  dyknow browse -k "AI工具" --count 30
  dyknow organize --dry-run       预览归档计划
  dyknow organize                 按分类归档
  dyknow index                    全量重建索引
  dyknow config                   查看当前配置与输出目录来源
  dyknow config --set-output "<目录>"   记住笔记输出目录（首次使用）
  dyknow reset --target transcribed --yes  把已转录项全部重置
        """,
    )

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("login", help="扫码登录")
    sub.add_parser("status", help="查看状态")

    sp = sub.add_parser("sync", help="批量同步收藏")
    sp.add_argument("--count", type=int, default=200)
    sp.add_argument("--only-index", action="store_true")
    sp.add_argument("--only-transcribe", action="store_true")
    sp.add_argument("--transcribe", action="store_true")
    sp.add_argument("--full", action="store_true")
    sp.add_argument("--output", default=None, help="笔记输出目录（本次运行覆盖配置）")

    # 单独的 transcribe 子命令（断点续转主入口）
    tp = sub.add_parser(
        "transcribe",
        help="批量转录（支持断点续转、Ctrl+C 安全退出、失败重试）",
    )
    tp.add_argument(
        "--count", type=int, default=0,
        help="本次最多处理的条目数，0=全部（默认 0）",
    )
    tp.add_argument(
        "--retry-failed", action="store_true",
        help="把 failed 状态的任务也纳入重试队列",
    )
    tp.add_argument(
        "--max-attempts", type=int, default=3,
        help="单条任务最大尝试次数，超过则跳过（默认 3）",
    )
    tp.add_argument("--output", default=None, help="笔记输出目录（本次运行覆盖配置）")

    # reset 子命令
    rp = sub.add_parser(
        "reset",
        help="把任务重置到 pending_index（强制重跑）",
    )
    rp.add_argument(
        "--target",
        choices=["transcribed", "failed", "all"],
        default="transcribed",
        help="重置哪类任务（默认 transcribed）；指定 --ids 时该项被忽略",
    )
    rp.add_argument(
        "--ids",
        default="",
        help="只重置这些 aweme_id：逗号/空白分隔，或一个每行一个 id 的文件路径",
    )
    rp.add_argument("--yes", "-y", action="store_true", help="跳过确认")

    # night 子命令
    np = sub.add_parser(
        "night",
        help="夜间批量转录（时间窗口内循环运行，超时自动停止）",
    )
    np.add_argument("--start", default="18:30", help="窗口开始时间（默认 18:30）")
    np.add_argument("--end", default="08:30", help="窗口结束时间（默认 08:30）")
    np.add_argument("--max-attempts", type=int, default=3, help="单条最大重试次数")

    pp = sub.add_parser("parse", help="单视频解析")
    pp.add_argument("text", help="包含抖音链接的文本")
    pp.add_argument("--output", "-o", default=None)
    pp.add_argument("--no-transcribe", action="store_true")

    # browse：关键词浏览 / 自动研究（场景 C）
    bp = sub.add_parser("browse", help="按关键词浏览抖音内容（场景 C）")
    bp.add_argument("-k", "--keyword", required=True, help="搜索关键词")
    bp.add_argument("--count", type=int, default=30, help="目标条数（默认 30）")
    bp.add_argument("--transcribe", action="store_true", help="同时下载并转录")
    bp.add_argument("--full", action="store_true", help="包含已同步过的内容")
    bp.add_argument("--output", default=None, help="笔记输出目录（本次运行覆盖配置）")

    # config：查看 / 设置持久化配置
    cp = sub.add_parser("config", help="查看或设置持久化配置（首次用来记住输出目录）")
    cp.add_argument(
        "--set-output", metavar="DIR", default=None,
        help="把笔记输出目录写入配置文件，之后长期生效",
    )

    # index：全量重建索引
    ip = sub.add_parser("index", help="全量重建知识库索引")
    ip.add_argument("--output", default=None, help="知识库根目录")
    ip.add_argument("--no-category-index", action="store_true", help="不生成分类索引")

    # organize：按分类安全归档
    op = sub.add_parser("organize", help="按分类安全移动笔记")
    op.add_argument("--output", default=None, help="知识库根目录")
    op.add_argument("--dry-run", action="store_true", help="只显示计划，不修改文件")
    op.add_argument(
        "--migrate", action="store_true",
        help="同时迁移旧「未分类/」目录中的笔记（默认不迁移）",
    )

    # taxonomy：查看分类注册表
    xp = sub.add_parser("taxonomy", help="查看分类注册表")
    xp.add_argument("--output", default=None, help="知识库根目录")
    xp.add_argument(
        "--paths", dest="paths_only", action="store_true",
        help="只输出分类路径列表",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "login": cmd_login,
        "status": cmd_status,
        "sync": cmd_sync,
        "transcribe": cmd_transcribe,
        "reset": cmd_reset,
        "parse": cmd_parse,
        "night": cmd_night,
        "browse": cmd_browse,
        "index": cmd_index,
        "organize": cmd_organize,
        "taxonomy": cmd_taxonomy,
        "config": cmd_config,
    }
    handler = handlers.get(args.command)
    return handler(args) if handler else 1


if __name__ == "__main__":
    sys.exit(main())
