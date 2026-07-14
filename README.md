# DyKnow

抖音视频 → 本地知识库 Markdown 笔记。

自动下载视频、语音转录文字、生成 Obsidian 兼容的 MD 文件。AI 摘要由调用方模型完成。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt
playwright install chromium

# 2. 下载转录模型（二选一）
#    方案A: ggml-tiny.bin (74MB, 推荐, 速度快)
#    方案B: ggml-small.bin (465MB, 精度略高)
#    放入 data/models/ 目录

# 3. 环境检测
python scripts/check_env.py

# 4. 扫码登录（打开浏览器扫码，Cookie 持久化）
python -m dyknow login

# 5. 开始使用
python -m dyknow parse "7.64 ... https://v.douyin.com/xxx/ ..."   # 单视频
python -m dyknow sync                                               # 同步收藏
python -m dyknow sync --transcribe                                  # 同步+转录
python -m dyknow status                                             # 查看状态
```

## 环境检测

首次使用前运行检测脚本，检查所有依赖是否就绪：

```bash
python scripts/check_env.py
```

检测项包括：Python 版本、依赖包、Playwright 浏览器、音频提取工具、转录模型、转录功能实测。

## 命令

| 命令 | 说明 |
|------|------|
| `login` | 扫码登录 |
| `status` | 查看状态 |
| `parse "<文本>"` | 单视频解析 |
| `sync` | 增量同步收藏 |
| `sync --transcribe` | 同步+下载+转录 |
| `sync --source collection` | 收藏夹精选 |
| `sync --count 100 --full` | 全量同步 |
| `browse -k "关键词"` | 主题浏览 |
| `browse -k "关键词" --transcribe` | 浏览+转录 |

## 工作流程

```
抖音内容 → 下载视频 → 语音转录文字 → Markdown笔记 → AI阅读总结
```

1. **抓取**: 通过 Playwright 模拟浏览器获取视频元数据
2. **下载**: 流式下载视频文件，支持断点续传
3. **转录**: pywhispercpp (whisper.cpp GGML) 将语音转为文字，CPU/GPU 通用
4. **生成**: 生成 Obsidian 兼容的 Markdown 笔记（frontmatter + 转录文本）
5. **AI 摘要**: 由调用方 AI 模型阅读转录文字，生成一句话总结、要点提炼、金句提取
6. **归类**: AI 根据内容自动将笔记归入知识库分类目录
7. **索引**: 在知识库根目录维护总索引文件，按分类汇总、支持快速定位

## 输出示例

```markdown
---
title: "AI工具推荐"
video_id: "7234567890123456789"
source: "https://www.douyin.com/video/7234567890123456789"
date: 2026-07-05
author: "科技博主"
status: done
---

# AI工具推荐

> [!info] 视频信息
> - **作者**: 科技博主
> - **时长**: 03:25
> - 👍 12,345 | 💬 567 | 🔄 890

## 📝 转录文本

今天给大家推荐几个好用的AI工具...

## 🤖 AI 摘要

### 一句话总结
推荐了5个实用的AI效率工具...

### 要点
- 工具A适合写作
- 工具B适合编程
```

## 依赖

- **核心**: Python 3.11+, requests, playwright
- **转录**: pywhispercpp (ggml-tiny 74MB / ggml-small 465MB, CPU/GPU 通用)
- **AI 摘要**: 调用方模型完成，无需额外配置

## 项目结构

```
DyKnow/
├── dyknow/              # 核心包
│   ├── main.py          # CLI 入口
│   ├── login.py         # 扫码登录 + Cookie 持久化
│   ├── scraper.py       # 内容爬取（3种来源）
│   ├── downloader.py    # 视频下载 + 断点续传
│   ├── transcriber.py   # 语音转录 (pywhispercpp ggml)
│   ├── generator.py     # Markdown 生成
│   ├── syncer.py        # 增量同步协调
│   ├── parser.py        # 单视频解析
│   ├── db.py            # SQLite 状态管理
│   └── config.py        # 配置
├── scripts/
│   ├── check_env.py     # 环境检测脚本
│   ├── organize_notes.py
│   └── gen_index.py
├── data/
│   ├── models/          # 转录模型文件
│   ├── videos/          # 视频缓存
│   └── dyknow.db        # SQLite 数据库
├── tests/
├── SKILL.md             # AI 模型使用说明
├── DESIGN.md            # 设计文档
└── README.md            # 本文件
```
