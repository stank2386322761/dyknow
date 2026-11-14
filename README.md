# DyKnow

> 把抖音收藏变成一个你自己的、能问能搜的知识库。本地跑，笔记是纯 Markdown，交给任何 AI 都能整理。

**收藏 → 自动转录 → 一条视频一篇笔记 → 交给 AI 整理 → Obsidian / 本地文件夹**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/) [![License](https://img.shields.io/badge/license-MIT-green)](LICENSE) [![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)]()

---

## 你会得到什么

你在抖音上收藏了几百上千条「以后要用」的视频，真到用的时候一条都翻不到。

DyKnow 做的事只有一件：**把你收藏的视频全部转录成文字，一条视频一个 Markdown 文件，放进你自己的文件夹。**

然后这些笔记就归你了——可以用 Obsidian 打开，可以扔给 Codex / Claude / 豆包 整理，可以让 AI 在里面搜「我之前收藏过讲选题的哪几条」。

```text
抖音知识库/
├── 索引.md                    ← 自动生成，全部笔记一览
├── 收藏/
│   └── 2026-09-30_某条视频标题_7650813418760359203.md
├── 链接/                      ← 别人分享给你的链接
├── 搜索/<关键词>/
├── 博主/<博主名>/
├── 对标/<关键词>/              ← 跨平台找对标的结果
└── 热榜/                      ← 每日热榜
```

一条笔记打开长这样（转写和评论默认折叠，第一屏只有 AI 写的内容）：

```markdown
# 某条视频标题

> 某博主 · 03:42 · 2026-09-30 · 👍1.2万 · 💬860 · ⭐5400

## 一句话
<!-- AI：… -->

## 要点
<!-- AI：… -->

> [!note]- 转写        ← 点击展开
> **[00:00]** 第一段……
```

---

## 能做什么

| 能力 | 命令 | 说明 |
|---|---|---|
| **收藏批量转笔记** | `dyknow update` | 同步新收藏 → 下载 → 转录 → 写笔记 → 更新索引，一条命令 |
| **处理分享链接** | `dyknow link "…"` | 一次可传多条，支持短链接和 `/shipin/` 专题页 |
| **搜关键词并转录** | `dyknow search 关键词` | 搜到的视频也能存成笔记 |
| **抓某个博主的作品** | `dyknow creator 主页链接` | 把对标账号的作品全扒成笔记 |
| **每日热榜** | `dyknow hot` | 抖音 / 知乎 / 微博 / B站 / 小红书 |
| **跨平台找对标** | `dyknow find 关键词 --days 30` | 四平台同题搜索，按点赞排序，生成对标报告 |
| **画面截图** | `dyknow frames` | 给能看图的 AI 用，自动在切镜头处截图 |
| **环境自检** | `dyknow doctor` | 显卡能不能用、浏览器、目录，有问题直接告诉你原因 |

---

## 为什么是它

**① 不需要显卡也能用**
有 NVIDIA 显卡就自动用 `large-v3-turbo` 本地转录，快且免费；没显卡就用硅基流动的云端转录（有免费额度）。显卡没被用上时，`dyknow doctor` 会直接告诉你卡在哪一步、该敲哪个命令，而不是让你猜。

**② 登录一次就够**
用一个固定的浏览器目录保持登录，不用复制 Cookie，不用每次重新扫码。
**没有屏幕的电脑**（云电脑、服务器、用手机上的 AI）会自动把二维码存成图片发给你，扫完回复一句就行，还支持短信验证码登录。

**③ 为 AI 设计，不是给 AI 堆料**
每条命令结束时输出「下一步该干什么」和一行 JSON 结果。能力弱一点的模型也不会卡死，你用的是 Codex、Claude、WorkBuddy 还是别的，AI 都能跑完流程。

**④ 笔记是你的，格式是最普通的**
纯 Markdown + frontmatter，没有数据库、没有私有格式、不锁在某个 App 里。整理模板可以自己写。

---

## 快速开始（Windows 为例）

需要 Python 3.10 及以上。

```bash
git clone https://github.com/stank2386322761/dyknow.git
cd dyknow
python -m venv .venv
.venv\Scripts\activate          # macOS / Linux：source .venv/bin/activate

pip install -e .                # 基础安装
pip install -e .[gpu]           # 有 NVIDIA 显卡再执行这一步
python -m playwright install chromium   # 电脑上没有 Chrome / Edge 时才需要

dyknow doctor                   # 检查环境，看到「显卡转录测试通过」才算真的用上了显卡
```

> 第一次运行 `doctor` 会下载约 1.6GB 的模型。没显卡可以直接跳过，改用云端转录。

然后：

```bash
dyknow config output_dir "D:/抖音知识库"   # 笔记存哪，Obsidian 仓库里的一个文件夹就行
dyknow login                              # 用抖音 App 扫码，只需一次
dyknow update                             # 同步新收藏并转录
```

Windows 用户也可以直接双击仓库里的 `dyknow.bat`。

---

## 让 AI 整理你的笔记

把仓库里的 [`SKILL.md`](SKILL.md) 交给你用的 AI（Codex、Claude Code、WorkBuddy 等都行）。

整理规则只有一条：把每个 `<!-- AI：… -->` 下面的内容按说明写好，再把 `status` 改成 `已整理`。

想换成自己的模板：

```bash
dyknow config template creator   # 用内置的「创作者」模板
dyknow config template "D:/我的模板.md"   # 或者自己写一个
```

---

## 完整命令

| 命令 | 作用 |
|---|---|
| `dyknow update` | 同步新收藏并转录（已有的跳过，不重复处理） |
| `dyknow link "分享文案"` | 处理分享链接，可一次传多个 |
| `dyknow search 关键词 --limit 20` | 搜索视频并转录 |
| `dyknow creator 主页链接 --limit 30` | 抓博主作品 |
| `dyknow transcribe [--retry-failed]` | 继续转录没转完的 |
| `dyknow frames [视频ID]` | 给已有笔记补画面截图 |
| `dyknow hot` | 今日热榜 |
| `dyknow find 关键词` | 跨平台找对标，生成对标报告 |
| `dyknow todo` | 列出等 AI 整理的笔记 |
| `dyknow index` | 重新生成索引 |
| `dyknow migrate 旧目录` | 把旧版 DyKnow 的笔记复制成新格式（旧文件不动） |
| `dyknow doctor` | 检查环境 |
| `dyknow config [项] [值]` | 查看 / 修改设置 |

所有命令加 `--no-transcribe` 时只建笔记、不转录。转录过程中随时 Ctrl+C，下次 `dyknow transcribe` 接着做。

**退出码**：`0` 成功 / `1` 出错 / `2` 需要扫码登录 / `3` 抖音或浏览器问题 / `4` 跑完了但有失败或被中断 / `5` 等待扫码或验证码

`--limit` 指的是**检查**最新多少条，已经有笔记的会跳过。

---

## 多平台选题

```bash
dyknow config niche "AI 工具实测和自媒体变现"   # 你的账号方向，AI 挑选题时参考
dyknow hot                                     # 今日热榜
dyknow find AI编程 --days 30                   # 四平台搜同题，按点赞排序
dyknow find AI编程 --days 30 --save 5          # 每平台前 5 条存成完整笔记
dyknow find 副业 --platforms xhs,zhihu         # 只搜小红书和知乎
```

| 平台 | 热榜 | 按热度搜索 | 搜索需要登录 | 存笔记时保存 |
|---|---|---|---|---|
| 抖音 | ✅ | ✅ 按点赞 | 不用 | 转录 + 热门评论 |
| 小红书 | 热门搜索词 | ✅ 按点赞 | 要 | 正文 + 图片（视频笔记转录） |
| 知乎 | ✅ | ✅ 按赞同 | 要 | 回答 / 文章全文 |
| B站 | ✅ | ✅ 按播放、点赞 | 不用 | 简介 + 转录 |
| 微博 | ✅ | — | 不用 | — |

所有平台都是打开网页、记录网页自己加载的数据，不破解签名；网页上看不到的抓不到。网站改版导致解析失败时，程序会提示，并把原始数据存在 `cache/raw/` 里方便修复。

---

## 设置项

运行 `dyknow config` 查看全部。常用的几项：

| 设置项 | 默认 | 说明 |
|---|---|---|
| `asr` | auto | `local` 本机转录 / `cloud` 硅基流动 / `none` 不转录 |
| `model` | auto | 有显卡用 large-v3-turbo，否则用 small |
| `language` | auto | 自动识别，中英混合也能识别 |
| `comments` | 5 | 每条笔记附带的评论数，0 不抓 |
| `frames` | 0 | 每条视频截几张画面，0 = 不截 |
| `niche` | 空 | 你的账号方向 |
| `template` | general | `general` / `creator` / 自定义模板路径 |
| `request_interval` | 1.5 | 请求间隔（秒），太小容易被限流 |

登录状态、模型和日志都存在数据目录里（Windows 上是 `%LOCALAPPDATA%\dyknow`），不放项目文件夹，也不会进 Git。

---

## 常见问题

**显卡没用上？** 运行 `dyknow doctor`，会给具体原因和解决命令。最常见是没装显卡运行库，`pip install -e .[gpu]`。

**抓取返回 0 条？** 程序会先用页面直取，取不到自动改成「打开页面边滚动边记录」。都失败会提示可能被限流，等一会儿再试，别连续重试。

**AI 卡在登录？** 登录必须你本人扫码。AI 看到退出码 2 会运行 `dyknow login`：有屏幕时弹浏览器；云电脑、手机端这类看不到窗口的地方，会把二维码存成图片发给你——保存到相册，用抖音「扫一扫 → 相册」扫。

**转录全部失败？** 多半是 PyAV 版本问题，见 [CHANGELOG](CHANGELOG.md#331--扫了码却登不上转录全部失败)。本项目已限制 `av<19` 并改为走 ffmpeg，`pip install -e .` 重装即可。

---

## 文档

- [SKILL.md](SKILL.md) —— 交给 AI 的说明，定义怎么整理笔记
- [CHANGELOG.md](CHANGELOG.md) —— 每个版本改了什么、已知限制
- [DESIGN.md](DESIGN.md) —— 模块结构与架构
- [docs/](docs/) —— 本机验证记录

---

## 声明

本项目仅供个人学习和整理自己的收藏使用。请遵守抖音的用户协议，控制抓取频率，不要用于商业采集。

思路参考了 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)（在已登录页面里取数据）和 [douyin-favorites-to-knowledge](https://github.com/tars1230/douyin-favorites-to-knowledge)（固定浏览器目录、图文作品识别），代码为独立实现。

MIT License
