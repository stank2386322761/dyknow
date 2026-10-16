# DyKnow

把抖音上的视频变成你自己的知识库：**收藏、分享链接、关键词搜索、博主作品** → 自动转录（中英混合）→ 每个视频一个 Markdown 笔记 → 交给任意 AI 按你的模板整理。

还能**找选题**：一条命令抓抖音、知乎、微博、B站、小红书的热榜；一条命令在小红书、知乎、B站、抖音上搜同一个关键词，只看最近的、按点赞排序，生成对标报告，让 AI 直接给选题建议。

- **登录一次就够**：用一个固定的浏览器目录保持登录，不用复制 Cookie，不用反复扫码
- **显卡转录**：有 NVIDIA 显卡自动用 large-v3-turbo；显卡没用上会直接告诉你原因
- **没显卡也行**：可以用硅基流动的云端转录（有免费额度）
- **一条命令跑完**：`dyknow update` = 同步新收藏 + 转录 + 写笔记 + 更新索引
- **对 AI 友好**：每条命令最后输出"下一步"和一行 JSON 结果，能力弱一点的模型也不会卡住
- **整理模板可换**：通用（一句话 + 要点）、创作者（加"可借鉴"），也可以自己写
- **画面截图**：给能看图的 AI 用，在画面变化（切镜头、换 PPT）时自动截图；图文作品直接保存原图

## 安装（Windows 为例）

需要 Python 3.10 及以上版本。

```bash
git clone https://github.com/stank2386322761/dyknow.git
cd dyknow
python -m venv .venv
.venv\Scripts\activate          # macOS / Linux：source .venv/bin/activate

pip install -e .                # 基础安装
pip install -e .[gpu]           # 有 NVIDIA 显卡再执行这一步（装显卡运行库）
python -m playwright install chromium   # 电脑上没有 Chrome / Edge 时才需要

dyknow doctor                   # 检查环境：显卡能不能用、浏览器、目录
```

`doctor` 会真实跑一次显卡转录测试（第一次会下载约 1.6GB 的模型）。看到「显卡转录测试通过」才说明显卡真的用上了。

## 开始使用

```bash
dyknow config output_dir "D:/抖音知识库"   # 笔记存哪（Obsidian 仓库里的一个文件夹就行）
dyknow login                              # 弹出浏览器，用抖音 App 扫码，只需一次
dyknow update                             # 同步新收藏并转录
```

## 命令

| 命令 | 作用 |
|---|---|
| `dyknow update` | 同步新收藏并转录（遇到已经有的就停，不会重复处理） |
| `dyknow link "分享文案"` | 处理分享链接，一次可以传多个；支持 `/video/`、短链接和 `/shipin/` 专题页主视频 |
| `dyknow search 关键词 --limit 20` | 搜索视频并转录 |
| `dyknow creator 主页链接 --limit 30` | 抓博主的作品 |
| `dyknow transcribe [--retry-failed]` | 继续转录没转完的，或重试失败的 |
| `dyknow frames [视频ID]` | 给已有笔记补上画面截图 |
| `dyknow hot` | 今日热榜（抖音 / 知乎 / 微博 / B站 / 小红书热搜） |
| `dyknow find 关键词` | 跨平台找对标，按点赞排序，生成对标报告 |
| `dyknow todo` | 列出等 AI 整理的笔记 |
| `dyknow index` | 重新生成索引（其他命令结束时会自动更新） |
| `dyknow migrate 旧目录` | 把旧版 DyKnow 的笔记复制成新格式（旧文件不动） |
| `dyknow doctor` | 检查环境 |
| `dyknow config [项] [值]` | 查看 / 修改设置 |

以上命令都加 `--no-transcribe` 时只建笔记、不转录。转录过程中可以随时按 Ctrl+C 停下，下次运行 `dyknow transcribe` 接着做。

`/shipin/` 路径中的编号是专题页编号，程序会读取页面明确标记的主视频，再按真实作品 ID 处理；不会自动选择相关推荐。主视频信息缺失或不唯一时会明确报错。

退出码：`0` 全部成功，`1` 出错，`2` 需要扫码登录，`3` 抖音或浏览器问题，`4` 跑完了但有条目失败或被中断（看 RESULT 里的 `failed` / `pending`）。

`--limit` 指的是**检查**最新多少条：`dyknow update --limit 5` 只看最新 5 条收藏，其中已经有笔记的会跳过。

## 找选题、找对标

```bash
dyknow config niche "AI 工具实测和自媒体变现"   # 你的账号方向，AI 挑选题时参考
dyknow login --site xhs                      # 小红书、知乎搜索需要登录（zhihu），B站、微博不用
dyknow hot                                   # 今日热榜 → 热榜/热榜_日期.md
dyknow find AI编程 --days 30                  # 四个平台搜「AI编程」，最近 30 天，按点赞排序
dyknow find AI编程 --days 30 --save 5         # 每个平台前 5 条存成完整笔记（正文、图片、转录）
dyknow find 副业 --platforms xhs,zhihu        # 只搜小红书和知乎
```

`find` 生成的报告在 `对标/<关键词>/对标报告_日期.md`：每个平台一张表（标题、作者、点赞、收藏、评论、发布日期），开头留了一块给 AI 写选题建议，默认按三条标准检查：会有人主动搜吗、能解决具体问题吗、看完有"终于说清楚了"的感觉吗。

| 平台 | 热榜 | 搜索 | 需要登录 | 存笔记时保存 |
|---|---|---|---|---|
| 抖音 | ✅ 热榜 | ✅ 按点赞 | 搜索不用 | 转录 + 热门评论 |
| 小红书 | 热门搜索词 | ✅ 按点赞 | 要 | 正文 + 图片（视频笔记转录） |
| 知乎 | ✅ 热榜 | ✅ 按赞同 | 搜索要 | 回答 / 文章全文 |
| B站 | ✅ 热门 | ✅ 按播放、点赞 | 不用 | 简介 + 转录 |
| 微博 | ✅ 热搜 | — | 不用 | — |

所有平台都是打开网页、记录网页自己加载的数据，不破解签名；网页上看不到的抓不到。网站改版导致解析失败时，程序会提示，并把原始数据存在数据目录的 `cache/raw/` 里，方便修复。

## 笔记长什么样

```text
抖音知识库/
├── 索引.md              ← 自动生成
├── 收藏/
├── 链接/
├── 搜索/<关键词>/
├── 博主/<博主名>/
├── 对标/<关键词>/         ← find 的报告和存下来的笔记（各平台混在一起）
└── 热榜/                 ← hot 的结果
```

每个视频一个文件：`日期_标题_视频ID.md`。打开后第一屏只有 AI 写的内容，转写和热门评论默认折叠：

```markdown
---
id: "7650813418760359203"
url: "https://www.douyin.com/video/7650813418760359203"
author: "某博主"
published: "2026-09-30"
tags: ["vibecoding", "一人公司"]
status: "已转录"
---

# 标题（太长时截到第一句，完整文案在「视频文案」里）

> 某博主 · 03:42 · 2026-09-30 · 👍1.2万 · 💬860 · ⭐5400 · [原视频](…)

## 一句话
<!-- AI：… -->

## 要点
<!-- AI：… -->

> [!note]- 转写
> **[00:00]** 第一段……

> [!note]- 热门评论
> - 👍2310 「……」
```

## 画面截图（给能看图的 AI）

```bash
dyknow config frames 8      # 以后每条视频最多截 8 张（0 = 关闭，默认关闭）
dyknow frames               # 给已有的笔记补截图
dyknow frames 7696110831217478927   # 只给某一条补
```

截图放在笔记旁边的 `附件/<视频ID>/` 文件夹里，笔记里多一个折叠的「画面」区块。截图时机是画面明显变化（切镜头、换 PPT、换场景）；画面几乎不动的口播视频，会改成按时间均匀截取。图文作品会直接保存原图。

能看图的 AI（GPT、Claude 等）整理时会顺带看截图，适合拆解画面上的字、演示步骤和产品界面。不能看图的模型会忽略截图，不受影响。

`status` 一共有六种：`待转录` / `已转录` / `已整理` / `转录失败` / `图文` / `无语音`。图文作品不转录（否则会把背景音乐的歌词写进笔记），纯音乐视频会标成"无语音"。

## 让 AI 整理

把 `SKILL.md` 交给你用的 AI（Codex、Claude Code、WorkBuddy 等都可以）。整理方式只有一条：在每个 `<!-- AI：… -->` 下面按说明写内容，然后把 status 改成 `已整理`。

换模板：`dyknow config template creator`，或者自己写一个 `.md` 模板：

```markdown
## 行动清单
<!-- AI：看完能马上照做的 1-3 个步骤 -->
```

然后运行 `dyknow config template "D:/我的模板.md"`。模板只对之后新建的笔记生效。

## 设置项

运行 `dyknow config` 可以查看全部设置。常用的几项：

| 设置项 | 默认 | 说明 |
|---|---|---|
| `asr` | auto | `local` 本机转录 / `cloud` 硅基流动 / `none` 不转录 |
| `model` | auto | 有显卡用 large-v3-turbo，否则用 small |
| `language` | auto | 自动识别语言，中英混合也能识别 |
| `comments` | 5 | 每条笔记附带的热门评论数，设为 0 不抓 |
| `frames` | 0 | 每条视频截几张画面，0 = 不截 |
| `niche` | 空 | 你的账号方向，热榜和对标报告里 AI 挑选题时参考 |
| `template` | general | `general` / `creator` / 自己写的模板文件路径 |
| `request_interval` | 1.5 | 两次请求之间的间隔（秒），太小容易被限流 |

登录状态、模型和日志都保存在数据目录里（Windows 上是 `%LOCALAPPDATA%\dyknow`），不放在项目文件夹里，也不会进 Git。

## 常见问题

**显卡没用上？** 运行 `dyknow doctor`，它会给出具体原因和解决命令。最常见的原因是没装显卡运行库，执行 `pip install -e .[gpu]` 就行。

**抓取返回 0 条？** 程序会先在页面里直接取数据，取不到会自动改成"打开页面边滚动边记录"。如果两种方式都失败，会提示你可能是被限流了：等一会儿再试，不要连续重试。

**AI 卡在登录？** 登录必须你本人扫码。AI 看到退出码 2 时，会请你运行 `dyknow login`。

## 声明

本项目仅供个人学习和整理自己的收藏使用，请遵守抖音的用户协议，控制抓取频率，不要用于商业采集。思路参考了 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)（在已登录的页面里取数据）和 [douyin-favorites-to-knowledge](https://github.com/tars1230/douyin-favorites-to-knowledge)（固定浏览器目录、图文作品识别），代码为独立实现。

MIT License
