<p align="center">
  <img src="docs/assets/daypilot-hero-banner.png" alt="DayPilot AI 日用工作台横幅" width="920">
</p>

<p align="center">
  <img src="docs/assets/daypilot-logo.png" alt="DayPilot AI 工作台 Logo" width="112">
</p>

<h1 align="center">DayPilot</h1>

<p align="center">一个由 SOUL.md 驱动的私人 AI 日用工作台。</p>

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

DayPilot 是一个本地优先、单用户的 AI 日用工作台。它不替你接管人生规划，只把长期方向、当前项目和今天真实可用的精力，压成可推进、可交付、可复盘的小目标。

它的核心入口是 `SOUL.md`。项目新增、改名、更新进度、调整项目今日目标、移除或完成项目，都通过编辑 `SOUL.md` 的 `## 当前项目` 段完成；前端只负责读取、刷新、反馈、check-in、周报和职业规划聊天。

真实模型只需要把 API Key 写进 `.env` 的 `DEEPSEEK_API_KEY`。不要把 API Key 写进 `SOUL.md`、README 或任何会提交到 git 的文件。

## 功能特性

**SOUL 驱动项目** - `SOUL.md` 是用户侧项目变更的唯一入口；Today 打开或刷新时会先导入最新项目状态。

**自由格式兼容** - 推荐一行一个项目，也支持自然语言段落；有 DeepSeek Key 时会优先用 LLM 解析，失败时保守回退。

**Today 目标** - 为 active 项目生成小而清楚的目标，包含完成标准、最低成果、时间估计和今天不做什么。

**反馈修正** - 在 Today 页面说“今天只有 45 分钟”或“这个太大了”，DayPilot 会生成新的目标版本。

**Check-in 复盘** - 晚上记录完成情况、主观难度和明日方向，项目当前进度会整理回写到 `SOUL.md`。

**周报复盘** - 周五基于 check-in 证据生成周报和下周重点，避免把未完成事项写成完成。

**职业规划聊天** - 结合 `SOUL.md`、能力状态、项目历史和近期记录，帮你整理空余时间、能力积累和作品方向。

**本地优先** - SQLite、日志、备份和真实 `SOUL.md` 默认都在本机；mock 模式不需要 API Key 也能跑通。

**无 P0 门槛** - 界面、README 和 `SOUL.md` 模板不要求 P0/P1/P2；旧数据仍可兼容，优先级只在用户明确表达时作为内部线索。

## 截图

<p align="center">
  <img src="docs/assets/daypilot-screenshot-today.png" alt="DayPilot Today 工作台截图" width="860">
</p>

## 快速开始

### Windows

复制配置和个人画像模板：

```bat
cd /d D:\path\to\DayPilot
copy .env.example .env
copy SOUL.example.md SOUL.md
notepad .env
notepad SOUL.md
```

如果先本地试跑，可以用 mock 模式：

```bat
set "DAYPILOT_LLM_MODE=mock" && python scripts\start_daypilot.py --restart
```

要使用真实模型，在 `.env` 写入：

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

## SOUL 写法

`SOUL.md` 的 `## 当前项目` 段推荐这样写：

```text
1. DayPilot：当前进度：正在打磨 SOUL 同步闭环。项目最终目标：形成稳定的个人工作节奏系统。项目今日目标：验证刷新 Today 能读取 SOUL 并生成可执行目标。
2. 训练项目：当前进度：已有数据方案草稿。项目最终目标：完成一套真实的 SFT + RL 训练记录。项目今日目标：补齐一条样例数据和评估标准。
```

也可以写成自然语言。比如“我现在主要推进 DayPilot，今天只想验证 SOUL 同步；训练项目先补数据方案”。有真实 DeepSeek Key 时，DayPilot 会优先解析这种自由文本；没有 Key 或解析不可靠时，会拒绝破坏性同步。

从 `## 当前项目` 删除某个 active 项目后，刷新 Today 会把它标记为 completed 并保留历史。当前没有 active 项目时，写 `暂无 active 项目。`。

## 架构

```text
backend/      API、服务、仓库、Schema
frontend/     Today、History、Weekly、Career Chat 页面
prompts/      目标生成与示例 Prompt
evals/        用例、rubric、评估脚本
scripts/      启动、停止、备份、连通性检查
data/         本地数据库、备份、临时文件、LLM 日志
docs/assets/  README 图片资源
```

核心数据流很短：`SOUL.md`、SQLite 项目和历史记录组成上下文；服务层调用 DeepSeek 或 mock 适配器；结果经过 schema 和质量检查后写回 SQLite，并在需要时同步回 `SOUL.md`；前端展示 Today、History、Weekly 和 Career Chat。

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
| Windows | 已支持，推荐 `scripts\start_daypilot.py` |
| macOS | 源码运行支持，使用 `python3 scripts/start_daypilot.py` |
| Linux | 源码运行支持，使用 `python3 scripts/start_daypilot.py` |
| 移动端浏览器 | 页面可浏览，服务仍需在一台电脑上启动 |

## 开发与验证

```bat
python scripts\check_deepseek_connection.py
python -m evals.run_all
python tests\frontend_api_smoke.py
python scripts\stop_daypilot.py
```

## 数据同步

- `/api/projects` 只作为只读状态来源保留。
- `/api/projects/lifecycle` 已禁用用户写入口，会返回 `410 project_lifecycle_disabled`。
- Today 的刷新会调用 SOUL 导入，把 `SOUL.md` 当前项目段同步到 SQLite。
- check-in 后的项目进度可以回写 `SOUL.md`，但不会把你已经从 SOUL 删除的 active 项目重新补回。
- 真实 `SOUL.md`、`.env`、数据库、备份和 LLM 日志不要提交到 git。

## 许可证

[Apache License 2.0](LICENSE)

## 链接

- [English README](README.en.md)
- [SOUL 示例](SOUL.example.md)
- [打包说明](docs/packaging.md)
