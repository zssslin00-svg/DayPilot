<p align="center">
  <img src="docs/assets/daypilot-hero-banner.png" alt="DayPilot AI 日用工作台横幅" width="920">
</p>

<p align="center">
  <img src="docs/assets/daypilot-logo.png" alt="DayPilot AI 工作台 Logo" width="112">
</p>

<h1 align="center">DayPilot</h1>

<p align="center">一个帮你把今天做扎实的私人 AI 工作台。</p>

<p align="center">
  <a href="README.en.md">English</a>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776AB">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-555">
  <img alt="LLM" src="https://img.shields.io/badge/LLM-DeepSeek-0F766E">
</p>

---

## DayPilot 是什么

DayPilot 是一个给个人用的 AI 日用工作台。它不负责接管你的人生，只帮你把长期方向、项目最终目标和今天能做的最小结果，压成一个可推进、可交付、可复盘的小目标。

它会读取 `SOUL.md` 和本地记录，记住你的项目、偏好、限制和反馈。当前项目可以写成推荐格式，也可以写成自然语言；DayPilot 会解析出当前进度、项目最终目标和项目今日目标，再同步回稳定格式。

日常循环很简单：工作日生成 Today 目标，白天可以用一句话改目标，晚上做 check-in，周五整理周报。Today 目标更新后会回写 `SOUL.md` 的项目今日目标，让第二天继续从真实状态出发。

## 功能特性

**SOUL 记忆** — `SOUL.md` 写长期方向、项目最终目标和项目今日目标；SQLite 记录日常反馈和版本历史。

**Today 目标** — 每个工作日为 active 项目生成小而清楚的目标，带完成标准、最低成果和时间估计。

**双目标项目** — 项目最终目标负责方向，项目今日目标负责当天约束。

**自由格式同步** — 可以在网页里更新项目，也可以随手改 `SOUL.md`；列表、非列表和自然语言都会尽量解析成固定项目格式。

**反馈修正** — 直接说“今天只有 45 分钟”或“这个太大了”，DayPilot 会重写 Today 目标并同步回项目今日目标。

**Check-in 复盘** — 晚上记录完成情况，项目当前进度会同步更新，但不会覆盖项目最终目标或项目今日目标。

**周报复盘** — check-in 会变成周报证据，周五自动整理本周进展、问题和下周重点。

**职业规划** — 聊空余时间、能力积累和作品方向，适合把零散想法整理成下一步行动。

**本地优先** — 数据库、日志、备份和真实 `SOUL.md` 默认都在本机；mock 模式不用 API Key 也能先跑通。

## 截图

<p align="center">
  <img src="docs/assets/daypilot-screenshot-today.png" alt="DayPilot Today 工作台截图" width="860">
</p>

## 快速开始

### Windows

先复制配置和个人画像模板：

```bat
cd /d D:\path\to\DayPilot
copy .env.example .env
copy SOUL.example.md SOUL.md
notepad .env
notepad SOUL.md
```

`SOUL.md` 当前项目推荐这样写，也可以先用自然语言描述，DayPilot 会在同步后整理：

```text
1. P0 DayPilot：当前进度：正在打磨目标同步。项目最终目标：形成稳定的个人工作闭环。项目今日目标：验证 Today 目标能回写 SOUL。
```

第一次可以用 mock 模式启动，不需要真实 API Key：

```bat
set "DAYPILOT_LLM_MODE=mock" && python scripts\start_daypilot.py --restart
```

要用真实模型，在 `.env` 里填：

```text
DAYPILOT_LLM_MODE=deepseek
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
```

然后启动：

```bat
python scripts\start_daypilot.py --restart
```

### macOS / Linux

```bash
cd /path/to/DayPilot
cp .env.example .env
cp SOUL.example.md SOUL.md
nano .env
nano SOUL.md
DAYPILOT_LLM_MODE=mock python3 scripts/start_daypilot.py --restart
```

启动后默认打开前端页面；后端服务在 `http://127.0.0.1:8000`。

## 架构

```text
backend/      API、服务、仓库、Schema
frontend/     页面、样式、前端请求
prompts/      目标生成与示例 Prompt
evals/        用例、rubric、评估脚本
scripts/      启动、停止、备份、连通性检查
data/         本地数据库、备份、临时文件
docs/assets/  README 图片资源
```

核心数据流很短：`SOUL.md`、SQLite 项目和历史记录组成上下文；服务层调用 DeepSeek 或 mock 适配器；结果经过 schema 和 Goal Critic 检查后写回 SQLite，并在需要时同步回 `SOUL.md`；前端展示 Today、History、Weekly、Project Update 和 Career Chat。

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 前端 | HTML + CSS + Vanilla JavaScript |
| 后端 | Python 3.10+ 标准库 HTTP 服务 |
| Agent 运行时 | DeepSeek OpenAI-compatible Chat Completions API |
| 本地数据 | SQLite |
| Fallback | deterministic mock adapters |
| 测试 | Python 测试脚本 + eval cases/rubrics |

## 平台支持

| 平台 | 状态 |
| --- | --- |
| Windows | 已支持，推荐用 `scripts\start_daypilot.py` |
| macOS | 源码运行支持，使用 `python3 scripts/start_daypilot.py` |
| Linux | 源码运行支持，使用 `python3 scripts/start_daypilot.py` |
| 移动端浏览器 | 页面可浏览，服务仍需在一台电脑上启动 |

## 开发

常用命令：

```bat
python scripts\check_deepseek_connection.py
python -m evals.run_all
python tests\frontend_api_smoke.py
python scripts\stop_daypilot.py
```

## 许可证

[Apache License 2.0](LICENSE)

## 链接

- [English README](README.en.md)
- [SOUL 示例](SOUL.example.md)
- [打包说明](docs/packaging.md)
