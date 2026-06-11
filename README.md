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

DayPilot 是一个给个人用的 AI 日用工作台。它不负责接管你的人生，只帮你把长期方向、当前项目和真实时间，压成今天能推进、能交付、能复盘的一小步。

它会读取 `SOUL.md` 和本地记录，记住你的项目、偏好、限制和反馈。你用得越久，它越知道什么目标适合你，什么目标看起来漂亮但当天做不完。

日常循环很简单：工作日生成今日目标，白天可以用一句话改目标，晚上做 check-in，周五整理周报。数据默认留在本机；只有你配置 DeepSeek 时，才会调用外部模型。

## 功能特性

**记忆** — `SOUL.md` 写长期方向，SQLite 记日常反馈；稳定偏好会慢慢沉淀下来。

**今日目标** — 每个工作日生成一个小而清楚的目标，带完成标准、最低成果和时间估计。

**反馈修正** — 直接说“今天只有 45 分钟”或“这个太大了”，DayPilot 会重写成更合适的版本。

**项目同步** — 可以在网页里更新项目，也可以改 `SOUL.md`；打开 Today 页时会同步项目状态。

**周报复盘** — check-in 会变成周报证据，周五自动整理本周进展、问题和下周重点。

**职业规划** — 聊空余时间、能力积累和作品方向，适合把零散想法整理成下一步行动。

**本地优先** — 数据库、日志、备份和真实 `SOUL.md` 默认都在本机，`.env` 专门放 API Key。

**轻量启动** — mock 模式可以先本地试跑；DeepSeek 模式再填 Key，适合一边用一边调。

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

核心数据流很短：`SOUL.md`、SQLite 项目和历史记录组成上下文；服务层调用 DeepSeek 或 mock 适配器；结果经过 schema 检查后写回 SQLite；前端展示 Today、History、Weekly、Project Update 和 Career Chat。

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
