# AI消息托管机器人 (tg-auto-reply)

基于 AI 的 Telegram 消息托管助手，让你在忙碌时也不会错过重要对话。

## 功能特点

- 🤖 **AI 智能回复** - 基于 OpenAI 兼容接口生成简短自然回复（附带最近 5 条上下文）
- 🔐 **安全加密** - Fernet 加密存储 Telegram 凭证与 session
- 📝 **透明日志** - 回复记录入库，/logs 可查看最近 5 条
- 🎛️ **完全控制** - 随时启停，一键退出
- ⚙️ **灵活配置** - /settings 调整延迟/群聊回复/黑白名单策略，/set_prompt 自定义提示词

## 快速开始

### 1. 安装

```bash
./install.sh
```

### 2. 配置

复制 `.env.example` 为 `.env` 并编辑：

```env
# Telegram Bot Token (从 @BotFather 获取)
BOT_TOKEN=your_bot_token_here

# 环境 (development / production)
ENVIRONMENT=development

# 访问控制（生产环境必填，逗号分隔 Telegram 用户 ID）
ALLOWED_TELEGRAM_IDS=
# 如需允许任意用户访问（不推荐），设置为 1
ALLOW_UNRESTRICTED_ACCESS=

# AI API 配置 (OpenAI 兼容)
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=https://api.siliconflow.cn/v1
AI_MODEL=deepseek-ai/DeepSeek-V3.2

# 可选配置
# 兼容别名（如果你已有这些变量名）
API_KEY=
API_BASE_URL=

# 加密密钥 (用于加密存储用户凭证，不设置将写入 data/encryption.key)
ENCRYPTION_KEY=
# 可选：自定义密钥文件路径
ENCRYPTION_KEY_FILE=

DATABASE_URL=sqlite+aiosqlite:///./data/bot.db
LOG_LEVEL=INFO
LOG_FILE=

# 运行保护
MAX_CONCURRENT_REPLIES=4
# 留空则自动派生（默认约为全局的一半）
MAX_CONCURRENT_REPLIES_PER_USER=
MAX_PENDING_REPLY_TASKS=200
# 留空则自动派生（默认约为全局的一半，且不超过 50）
MAX_PENDING_REPLY_TASKS_PER_USER=
AUTO_REPLY_COOLDOWN_SECONDS=15
# 可选：内存级冷却窗口清理（秒）
INFLIGHT_COOLDOWN_TTL_SECONDS=
# 可选：内存上下文长度（条消息，默认 10）
CONTEXT_MAX_MESSAGES=10
# 可选：内存上下文最大对话数（默认 1000）
CONTEXT_CACHE_MAX_CHATS=1000
# 可选：内存上下文 TTL（秒，默认 21600）
CONTEXT_TTL_SECONDS=21600
SHUTDOWN_GRACE_PERIOD_SECONDS=10
AI_TIMEOUT_SECONDS=15
AI_MAX_RETRIES=1
# AI 熔断保护（连续失败 N 次后暂停请求）
AI_CIRCUIT_FAILURE_THRESHOLD=5
AI_CIRCUIT_RESET_SECONDS=60
INSTANCE_LOCK_FILE=

# 异步日志（默认启用）
ENABLE_ASYNC_LOGGING=1
LOG_QUEUE_MAXSIZE=1000
LOG_BATCH_SIZE=20
LOG_BATCH_INTERVAL=1.0

# 客户端断线重连
CLIENT_RECONNECT_INITIAL_SECONDS=1
CLIENT_RECONNECT_MAX_SECONDS=30

# 启动自检
ENABLE_STARTUP_HEALTHCHECKS=1

# 告警检测
ALERT_LOOKBACK_LINES=500
ALERT_KEYWORDS=AI 回复失败|回复队列已满|database is locked|登录已失效|terminated by other getUpdates

# 日志清理
LOG_RETENTION_DAYS=90

# 数据库健壮性
DB_BUSY_TIMEOUT_MS=30000
DB_JOURNAL_MODE=WAL
DB_SYNCHRONOUS=NORMAL
```

### 3. 运行

```bash
source .venv/bin/activate
python main.py
```

## 使用流程

1. 在 Telegram 中找到你的机器人（确保与 `BOT_TOKEN` 对应的 Bot）
2. 仅响应命令，建议在私聊中操作
3. 发送 `/start` 开始
4. 发送 `/login` 登录你的 Telegram 账号（输入 API_ID / API_HASH / 验证码）
5. 发送 `/start_hosting` 开始自动回复
6. AI 回复发生在你的**用户账号会话**中，不会出现在与 Bot 的聊天里
7. 交互消息使用纯文本展示，避免 Markdown 语法符号泄露

## 命令列表

| 命令 | 说明 |
|------|------|
| `/start` | 开始使用 |
| `/help` | 帮助信息 |
| `/login` | 登录 Telegram 账号 |
| `/logout` | 退出登录并清除数据 |
| `/status` | 查看当前状态 |
| `/start_hosting` | 开始自动回复 |
| `/stop_hosting` | 停止自动回复 |
| `/logs` | 查看回复日志 |
| `/stats` | 查看统计信息 |
| `/settings` | 查看/修改设置 |
| `/set_prompt` | 设置自定义提示词 |
| `/whitelist` | 管理白名单 |
| `/blacklist` | 管理黑名单 |
| `/about` | 关于本机器人 |
| `/cancel` | 取消当前操作 |

> 如未看到命令菜单，可稍等 Telegram 同步，或使用 BotFather 手动设置命令。

## 设置示例

- `/settings ai on` 开启 AI 回复
- `/settings delay 5` 设置回复延迟 5 秒
- `/settings groups on` 允许群聊回复
- `/whitelist add 123456` 添加白名单（也可回复/转发消息后执行 add）

## 工作原理

- 机器人仅作为控制面板使用；登录后由 Telethon 以**你的 Telegram 用户账号**监听并回复消息。
- AI 回复基于 OpenAI 兼容接口生成，默认使用 `AI_MODEL`，并带入同一对话最近 5 条日志作为上下文。

## 生产运行建议

- 生产环境务必设置 `ENVIRONMENT=production` 并提供 `ENCRYPTION_KEY`（或 `ENCRYPTION_KEY_FILE`），避免重启/迁移后无法解密已保存的凭证。
- 生产环境必须配置 `ALLOWED_TELEGRAM_IDS`（或显式设置 `ALLOW_UNRESTRICTED_ACCESS=1`）。
- 建议启用日志文件（`LOG_FILE`）并定期轮转。
- 建议设置 `INSTANCE_LOCK_FILE`，避免多实例导致 polling 冲突。
- SQLite 已启用 WAL 与 busy_timeout，生产环境默认启用单实例锁，避免多实例导致锁冲突；高并发场景建议迁移到更稳健的数据库。
- 备份：至少备份 `data/bot.db` 与 `data/encryption.key`。
- 升级版本前建议执行 `python scripts/migrate.py`，确保 schema 版本一致。

## 监控与健康检查（可选）

设置 `HEALTHCHECK_PORT` 或 `ENABLE_HTTP_HEALTHCHECK=1` 后，会启动轻量 HTTP 服务：

- `GET /healthz` 存活探针
- `GET /readyz` 就绪探针（含 DB & schema 检查）
- `GET /metrics` Prometheus 指标

可选设置 `HEALTHCHECK_TOKEN`，请求时需携带：
`X-Health-Token: <token>` 或 `Authorization: Bearer <token>`。
生产环境若对外暴露端口，必须设置 `HEALTHCHECK_TOKEN`。

> 容器场景建议把健康检查配置到编排层（如 docker-compose / K8s），
> 并确保探针端口与 `HEALTHCHECK_PORT` 一致；
> 若启用了 `HEALTHCHECK_TOKEN`，探针也需携带 token。

详细运维说明见 `OPERATIONS.md`。

## 获取 API 凭证

1. 访问 https://my.telegram.org
2. 登录你的 Telegram 账号
3. 点击 "API development tools"
4. 创建应用，获取 `api_id` 和 `api_hash`

## 安全说明

- 所有凭证使用 Fernet 加密存储
- Session 数据本地保存，不上传任何服务器
- 代码开源，可自行审计
- 随时可以 `/logout` 清除所有数据
- 加密密钥来自 ENCRYPTION_KEY 或本地 `data/encryption.key`（可用 ENCRYPTION_KEY_FILE 指定路径）
- 登录过程中会尝试自动删除敏感消息，若仍可见请手动删除

## 技术栈

- Python 3.10+
- python-telegram-bot (Bot 交互)
- Telethon (用户客户端)
- OpenAI 兼容 API (AI 回复)
- SQLAlchemy + SQLite (数据存储)
- Cryptography (加密)

## 项目结构

```
├── main.py              # 程序入口，初始化 Bot 和信号处理
├── src/
│   ├── bot/
│   │   ├── handlers.py  # Bot 命令处理器（登录、设置、托管控制等）
│   │   └── messages.py  # 消息模板
│   ├── client/
│   │   └── manager.py   # Telethon 客户端管理（消息监听、会话管理）
│   ├── ai/
│   │   └── chat.py      # AI 回复生成（OpenAI 兼容接口）
│   ├── db/
│   │   ├── models.py    # 数据库模型定义
│   │   └── database.py  # 数据库连接与初始化
│   └── utils/
│       └── crypto.py    # Fernet 加密工具
├── scripts/
│   ├── backup.sh        # 数据库与密钥备份脚本
│   ├── cleanup_logs.py  # 日志清理脚本
│   ├── check_alerts.sh  # 告警检测脚本
│   └── install_cron.sh  # 定时任务安装脚本
├── data/                # 数据存储目录（bot.db, encryption.key）
├── sessions/            # Telegram session 文件（已弃用，现使用数据库存储）
├── install.sh           # 一键安装脚本
└── .env.example         # 配置模板
```

## 文档入口

- 用户指南：`docs/USER_GUIDE.md`
- 生产运维：`OPERATIONS.md`
- 部署指南：`docs/DEPLOYMENT.md`
- API 与命令说明：`docs/API.md`
- 开发者指南：`docs/DEVELOPMENT.md`
- 故障排查：`docs/TROUBLESHOOTING.md`
- 变更记录：`CHANGELOG.md`

## 测试与质量

```bash
source .venv/bin/activate
pytest
```

默认要求覆盖率 ≥80%（见 `pyproject.toml`）。

## 依赖锁定

生产环境建议使用 `requirements.lock` 安装依赖：

```bash
pip install -r requirements.lock
pip install -e .
```

## 数据库模型

| 表名 | 说明 |
|------|------|
| `users` | 用户基本信息与托管状态 |
| `user_credentials` | 加密存储的 Telegram API 凭证与 session |
| `user_settings` | 用户个性化设置（AI 开关、延迟、提示词等） |
| `message_logs` | 消息回复日志（原始消息、AI 回复、状态） |
| `contact_lists` | 白名单/黑名单联系人 |

## 消息状态说明

| 状态 | 说明 |
|------|------|
| `sent` | 回复已成功发送 |
| `failed` | AI 生成或发送失败 |
| `skipped` | 因设置（黑名单/白名单/群聊过滤）跳过 |
| `cooldown` | 冷却时间内，跳过回复 |
| `dropped` | 队列已满，消息被丢弃 |
| `cancelled` | 任务被取消（停止托管/登出时） |

## 许可证

MIT License
