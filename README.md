<p align="center">
  <img src="docs/assets/daypilot-hero-banner.png" alt="DayPilot AI 日用工作台横幅" width="920">
</p>

<p align="center">
  <img src="docs/assets/daypilot-logo.png" alt="DayPilot AI 工作台 Logo" width="112">
</p>

<h1 align="center">DayPilot</h1>

<p align="center">一个本地优先、私有可控、越用越懂你的 AI 日用工作台：根据个人画像、项目进展和每日反馈自动调整目标，并生成周报复盘。</p>

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

DayPilot 是一个本地优先、单用户、私人可控的 AI 日用工作台，围绕“每天约 4 小时有效工作时间”设计。它不会替你做宏大人生规划，而是把长期方向、当前项目、偏好和限制，压缩成今天能交付、能检查、能复盘的小目标。

它会从 `SOUL.md`、SQLite 用户画像、项目历史、反馈修正、check-in 和周报偏好里持续理解你的个人习惯。用得越久，DayPilot 越能贴近你的工作节奏、产出偏好、精力边界和职业发展方向。

它的核心循环很短：

1. 工作日读取你的长期上下文、当前项目和画像信息，生成今日目标。
2. 白天可以用反馈修正目标，例如缩小范围、改变产出形式、调整时间预算。
3. 晚上提交 check-in，记录完成情况、主观难度、项目进展和明日方向。
4. 周五基于一周记录生成周报、下周重点，并吸收稳定偏好影响后续生成。

AI 能力不只负责“生成一条任务”，还会参与项目状态整理、今日目标重写、周报重点调整、用户画像建议和职业规划对话。数据默认都在本机：SQLite 数据库、LLM 日志、备份文件和你的真实 `SOUL.md` 都不会上传到外部服务，除了你主动配置的 DeepSeek API 调用。

## 功能特性

**本地私有工作台**：默认把 SQLite 数据库、LLM 日志、备份文件和真实 `SOUL.md` 留在本机，适合沉淀个人项目、偏好、复盘和职业上下文。

**越用越懂你的个人画像**：从稳定上下文、日常反馈、check-in、周报偏好和职业规划聊天中识别习惯与约束；AI 判断为明确、稳定且值得长期保存的信息会自动写入 SQLite 和 `SOUL.md`。

**AI 项目与目标自动调整**：根据当前 active 项目、项目状态变化和当天反馈生成小而可交付的目标，带完成标准、最低成果、时间估计和难度；项目名、摘要、规划指导或优先级变化后，也会刷新相关今日目标。

**反馈修正与长期记忆**：在 Today 页面输入“今天只有 45 分钟”“这个太大了”“我更想写代码”，DayPilot 会生成新的目标版本；当反馈里出现稳定偏好，例如“以后不要给纯学习目标”“每次都要有可验收产出”，系统会把它沉淀为长期记忆。

**自然语言项目更新**：用自然语言添加项目、标记项目完成、更新项目状态，或直接编辑 `SOUL.md` 的“当前项目”段落后点击 Today 页“刷新”，系统会同步当前项目列表，并把项目事实用于后续目标与复盘。

**AI 周报复盘**：日终 check-in 会记录完成文本、完成状态、体感难度和明日方向，周五基于这些证据生成周报、下周重点，并支持继续反馈生成新版本。

**职业规划助手**：在 **职业规划** Tab 里和私人职业发展规划助手对话。它会读取 `SOUL.md`、结构化用户画像、项目历史、能力状态和最近记录，帮你判断空余时间适合做什么、怎样积累可迁移能力和作品证据。

## 个人上下文怎么输入

DayPilot 需要先了解你是谁、你在做什么、你喜欢怎样工作。输入入口分为“启动前的稳定上下文”和“网页里的日常上下文”。这些上下文会影响后续今日目标、项目调整、周报复盘、职业规划建议和内容生成口径。

| 入口 | 在哪里输入 | 输入什么 | 系统如何使用 |
| --- | --- | --- | --- |
| DeepSeek 配置 | `.env` | `DEEPSEEK_API_KEY`、模型和超时配置 | 只有 `DAYPILOT_LLM_MODE=deepseek` 时必须存在 API Key；真实 LLM 路径用它生成目标、解释反馈、整理项目状态、生成周报和职业规划回复。 |
| 稳定个人画像 | `SOUL.md`，由 `SOUL.example.md` 复制而来 | 长期方向、当前技能点、性格与工作方式、发展意愿、职业价值观与约束、当前项目边界、用户偏好、避免事项、时间预算、目标生成原则 | 每次 Agent 调用都会读取，作为长期上下文，影响目标、周报、职业建议和后续内容生成。适合写长期稳定的信息，不适合写当天临时情况。 |
| 职业规划聊天 | 网页左侧 **职业规划** | 空余时间、职业困惑、想发展的方向、当前技能和性格补充 | 给出方向判断、澄清问题、项目建议、风险提醒和下一步行动；发现明确稳定的新画像信息时，会自动写入 SQLite 和 `SOUL.md`。 |
| 项目变化 | 网页左侧 **项目更新**，或编辑 `SOUL.md` 后点击 Today 页 **刷新** | “新增项目：... 当前进度：... 目标：...”，或在“当前项目”段落维护编号/项目符号列表 | 写入 SQLite 项目表，并更新 `SOUL.md` 的当前项目段落；从该列表消失的 active 项目会标记完成并保留历史。 |
| 当天偏好/约束 | Today 页 **反馈修正** | “今天只有 30 分钟”“这个目标太大”“更想做实验”“以后不要给抽象目标” | 先修正今日目标；如果是稳定偏好或避免模式，会沉淀为长期记忆。 |
| 日终事实 | Today 页 **Check-in** | 完成状态、完成说明、体感难度、明日方向 | 作为历史记录、项目进展、周报证据和次日目标承接。 |
| 周报偏好 | Weekly 页 **周报修改意见** | “下周计划要更可验收”“不要写成流水账” | 生成新的周报版本，并保存稳定的周报偏好。 |

建议第一次启动前先复制 `SOUL.example.md` 为 `SOUL.md`，再编辑 `SOUL.md`，至少写清楚这些内容：

```markdown
## 长期方向

我长期想形成什么能力，或者希望项目最终服务什么方向。

## 当前项目

1. 项目名：当前阶段、最近阻塞、希望今天推进到什么程度。

## 当前技能点

- Python / 前端 / 后端 / 数据分析 / LLM 应用等，并补充证据。

## 性格与工作方式

- 我更适合项目驱动学习，偏好能留下产出的计划。

## 发展意愿

- 我希望未来加深的方向、想转向的领域、想形成的作品集。

## 职业价值观与约束

- 我重视长期复利、真实可用时间、精力边界和可展示成果。

## 用户偏好

- 我喜欢小而可交付的目标。
- 我希望目标最后留下代码、文档、实验记录或决策笔记。

## 避免事项

- 不要把长期愿望压成一天任务。
- 不要给纯阅读、纯观看、纯思考的目标，除非它会留下产出。
```

不要把 API Key、账号密码、私密 token 写进 `SOUL.md` 或 README。API Key 只放在 `.env` 或系统环境变量里。

## 截图

### Today 工作台

<p align="center">
  <img src="docs/assets/daypilot-screenshot-today.png" alt="DayPilot Today 工作台截图" width="860">
</p>

### History 最近记录

<p align="center">
  <img src="docs/assets/daypilot-screenshot-history.png" alt="DayPilot History 最近记录截图" width="860">
</p>

## 快速开始

“任何电脑可启动”在这里指：Windows、macOS 或 Linux 上安装了 Python 3.10+。使用 mock 模式不需要 DeepSeek Key；使用真实 DeepSeek 模式时，需要可以访问 DeepSeek API，并且你有有效的 `DEEPSEEK_API_KEY`。DayPilot 当前不需要 `npm install` 或额外 Python 依赖。

> **重要：配置完 API Key 后，记得回到 DayPilot 页面点击「刷新」按钮，让新配置和最新上下文立即生效。**

### Windows

```bat
cd /d D:\path\to\DayPilot
copy .env.example .env
copy SOUL.example.md SOUL.md
notepad .env
notepad SOUL.md
python scripts\start_daypilot.py
```

如果 Windows 提示 `python` 不可用，但已安装 Python Launcher，可以把最后一行换成 `py -3 scripts\start_daypilot.py`；也可以直接运行 `scripts\start_daypilot.bat`，它会自动尝试这两种入口。

本地 mock 调试可以不配置真实 DeepSeek Key。开发时推荐使用 `--restart`，它会清掉旧的 DayPilot 后端/前端进程、重新备份并启动服务、用无缓存静态服务器提供前端，并打开带时间戳的页面：

```bat
cd /d D:\path\to\DayPilot
set "DAYPILOT_LLM_MODE=mock" && python scripts\start_daypilot.py --restart
```

### macOS / Linux

```bash
cd /path/to/DayPilot
cp .env.example .env
cp SOUL.example.md SOUL.md
nano .env
nano SOUL.md
python3 scripts/start_daypilot.py
```

`.env` 至少需要：

```text
DAYPILOT_LLM_MODE=deepseek
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
```

源码开发启动脚本会固定使用项目内 `.env`、`SOUL.md` 和 `data/`，并自动完成这些事：在真实 LLM 模式下检查 `DEEPSEEK_API_KEY`、备份已有数据库、首次运行时初始化 SQLite、启动后端 `http://127.0.0.1:8000`、启动前端 `http://127.0.0.1:5173/pages/index.html`，并打开浏览器。使用 `--restart` 时会先停止默认端口上的旧 DayPilot 开发或打包进程，避免端口占用、旧静态资源缓存或重复服务导致页面不是最新版本。

停止服务：

```bat
python scripts\stop_daypilot.py
```

停止脚本会清理项目内 pid 文件，并检查 8000/5173 默认端口上的 DayPilot 进程。

macOS / Linux 使用：

```bash
python3 scripts/stop_daypilot.py
```

真实模型连通性检查：

```bat
python scripts\check_deepseek_connection.py
```

## 架构

```text
backend/api/             HTTP API 入口，基于 Python 标准库
backend/services/        每日目标、反馈修正、项目进展、周报、职业规划聊天、SOUL 同步
backend/repositories/    SQLite 读写封装
backend/schemas/         Agent 结构化输出 JSON Schema
frontend/pages/          单页工作台 HTML
frontend/services/       前端 API 调用和页面交互
frontend/styles/         页面样式
prompts/                 目标生成 Prompt 和示例
evals/                   Agent 行为评估用例、rubric 和脚本
scripts/                 启动、停止、备份、恢复、连通性检查
data/                    本地数据库、备份、临时文件和 LLM 日志
docs/                    README 图片资产和公开运行说明
```

核心数据流：

1. `SOUL.md`、SQLite 用户画像、项目列表和历史记录组成上下文。
2. 服务层调用 DeepSeek OpenAI-compatible Chat Completions API，要求返回 JSON。
3. JSON 通过 schema、归一化和质量检查后写入 SQLite。
4. 前端读取 API，展示 Today、History、Weekly、Project Update 和 Career Chat。
5. 如果 `SOUL.md` 同步失败，失败任务会进入 SQLite retry queue，后台维护循环会重试。
6. 职业规划聊天保存会话和消息；AI 判断为可沉淀的画像更新会直接合并到 `user_profile.career_profile` 并同步 `SOUL.md`，同时保留 applied 审计记录。

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 前端 | HTML + CSS + Vanilla JavaScript |
| 后端 | Python 3.10+ 标准库 `ThreadingHTTPServer` |
| Agent 运行时 | DeepSeek OpenAI-compatible Chat Completions API |
| Fallback | Deterministic mock adapters，用于测试和故障兜底 |
| 数据库 | SQLite |
| 本地服务 | `scripts/serve_frontend.py` 无缓存静态前端 + Python 后端 |
| 测试 | 自包含 Python 测试脚本 + eval cases/rubrics |

## 平台支持

| 平台 | 状态 |
| --- | --- |
| Windows | 已支持：`scripts\start_daypilot.py`，并保留 `.bat` wrapper。 |
| macOS | 源码运行支持：使用 `python3 scripts/start_daypilot.py`。 |
| Linux | 源码运行支持：使用 `python3 scripts/start_daypilot.py`。 |
| 移动端浏览器 | 页面有响应式布局；服务仍需要在一台电脑上启动。 |

## 开发与验证

运行所有 eval：

```bat
python -m evals.run_all
```

运行后端测试：

```bat
for %f in (backend\tests\test_*.py) do python %f
```

macOS / Linux：

```bash
for f in backend/tests/test_*.py; do python3 "$f"; done
```

运行前端/API smoke：

```bat
python tests\frontend_api_smoke.py
```

恢复最新备份：

```bat
python scripts\restore_db.py
```

Windows 也可以使用：

```bat
scripts\restore_latest_db.bat
```

## 数据怎么自动同步

- 你不用记住数据库字段。DayPilot 会把项目的最新状态整理好，前端看到的项目摘要、进度和规划建议都从这里来。
- 你可以在网页里更新项目，也可以直接改 `SOUL.md` 的“当前项目”。点 Today 页的“刷新”后，新项目会导入，改名、进度和优先级会同步；从列表里移走的项目会标记完成，不会删掉历史。
- 项目发生明显变化时，今天的目标会跟着刷新，避免继续沿用旧项目名或过期目标。
- 每次生成、重新生成或用反馈修正今日目标，History 都会显示当前最新版本；旧版本还留在后台，方便以后回看。
- 如果你编辑了当天 check-in，系统会用新内容替换旧进展，不会让过期记录继续影响项目状态。
- 职业规划聊天会保存对话，也会自动沉淀明确、稳定、证据充分的新技能、偏好、约束或发展方向。
- 职业规划聊天的画像沉淀只会更新个人画像并同步到 `SOUL.md`；不会自动创建项目、刷新目标或写 check-in。

## API Surface

- `GET /health`
- `GET /api/today-goal`
- `GET /api/history?days=30`
- `GET /api/projects`
- `POST /api/checkin`
- `POST /api/today-goal/regenerate`
- `POST /api/goal-feedback`
- `POST /api/projects/lifecycle`
- `POST /api/soul-sync/import-projects`
- `POST /api/weekly-report/generate`
- `POST /api/weekly-report/feedback`
- `POST /api/career-chat`
- `GET /api/career-chat/sessions`
- `GET /api/career-chat/history?session_id=...`
- `POST /api/career-chat/profile-suggestion`（legacy：仅用于处理旧数据里的 pending 画像建议）
- `GET /api/soul-sync/status`
- `POST /api/soul-sync/retry`

## 数据安全

- DayPilot 默认把个人数据库、画像、备份和日志保留在本机；真实 LLM 模式只会向你配置的 DeepSeek API 发送完成当前 AI 功能所需的上下文。
- `.env` 被 git 忽略，里面只放本机 API Key。
- `data/db/`、`data/backups/`、`data/tmp/`、`data/llm_logs/` 默认被 git 忽略。
- LLM 日志不会写入 API Key 或 Authorization header。
- 启动脚本会在服务启动前备份已有 SQLite 数据库。
- 职业规划聊天可能把对话内容发送给你配置的 DeepSeek；使用 `DAYPILOT_LLM_MODE=mock` 时走本地 deterministic fallback。
- 职业规划聊天会自动写入 AI 判断为明确稳定的画像线索；临时情绪、一次性约束和没有证据的猜测不应被沉淀。
- 上传 GitHub 前不要提交个人数据库、LLM 日志或私密版 `SOUL.md`；仓库只保留 `SOUL.example.md`。

## 许可证

[Apache License 2.0](LICENSE)

## 链接

- [SOUL.example.md](SOUL.example.md)
