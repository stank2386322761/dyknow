# DyKnow 设计文档

## 定位

**DyKnow** = Douyin + Knowledge。将抖音视频内容自动化转化为本地知识库 Markdown 笔记。

核心链路：
```
抖音内容 → 下载视频 → 语音转录文字 → Markdown笔记 → 模型阅读总结
```

## 三种场景

| 场景 | 命令 | 输入 | 流程 |
|------|------|------|------|
| A 单视频 | `parse` | 聊天文本中的链接 | 提取链接→下载→转录→生成MD |
| B 批量同步 | `sync` | 用户收藏/点赞列表 | 爬取列表→增量同步→批量转录 |
| C 主题浏览 | `browse` | 搜索关键词 | 搜索→抓取列表→下载→转录 |

## 架构

```
CLI ── main.py
  ├── login        Playwright 扫码 → Cookie 持久化
  ├── scraper      3种来源: favorite / listcollection / search
  ├── syncer       增量协调: 阶段1(索引) → 阶段2(转录)
  ├── parser       单视频: 正则提取链接 → 元数据 → 转录
  ├── downloader   流式下载 + 断点续传
  ├── transcriber  ffmpeg/av 提取音频 → pywhispercpp ggml 转录
  ├── generator    Obsidian Markdown 生成
  ├── db           SQLite 状态管理
  └── config       配置 + 环境变量
```

## 设计原则

1. **视频→文字是核心**：下载+转录是整个工具链的基础，AI 摘要由调用者完成
2. **增量同步**：SQLite 记录每条的处理状态，不重复处理
3. **分阶段**：阶段1快速索引（秒级），阶段2深度转录（小时级）
4. **优雅降级**：转录失败不影响索引，任何组件缺失都有清晰提示
5. **文本模型友好**：禁止浏览器看视频，强制走转录管道

## 状态流转

```
pending → indexed → downloaded → transcribed → done
                                    ↘ failed (重试)
```

## API 来源

| 源 | API | 翻页 | 说明 |
|----|-----|------|------|
| favorite | `/aweme/v1/web/aweme/favorite/` | 游标翻页(20条/页) | 点赞+收藏全量 |
| collection | `/aweme/v1/web/aweme/listcollection/` | 页面拦截(10条/页) | 收藏夹精选 |
| search | `/aweme/v1/web/search/item/` | 偏移翻页(15条/页) | 关键词搜索 |

抖音网页版不严格区分"点赞"和"收藏"，默认使用 favorite 源获取全部互动内容。

## 目录结构

```
DyKnow/
├── dyknow/            # 核心包
│   ├── main.py        # CLI 入口
│   ├── login.py       # 扫码登录 + Cookie
│   ├── scraper.py     # 内容爬取
│   ├── syncer.py      # 同步协调
│   ├── parser.py      # 单视频解析
│   ├── downloader.py  # 视频下载
│   ├── transcriber.py # 语音转录
│   ├── generator.py   # MD 生成
│   ├── db.py          # SQLite
│   └── config.py      # 配置
├── data/              # Cookie / DB / 视频缓存
├── tests/             # 测试
├── DESIGN.md          # 本文件
├── README.md          # 用户文档
├── SKILL.md           # 模型指令
└── requirements.txt
```

## 依赖策略

- 核心（必装）：requests, playwright
- 转录：pywhispercpp (ggml-tiny 74MB / ggml-small 465MB) — CPU/GPU 通用，无需网络下载，最稳定
- AI 摘要：调用方模型完成，不依赖外部 API
