# 开发者指南

本文档面向希望参与开发或二次开发的开发者。

## 开发环境搭建

### 前置要求

- Python 3.10+
- Git

### 安装步骤

```bash
# 克隆项目
git clone <repo-url>
cd tg-auto-reply

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 安装运行依赖（推荐锁定版本）
pip install -r requirements.lock
pip install -e .

# 安装开发依赖
pip install -r requirements-dev.txt
```

### 配置开发环境

```bash
cp .env.example .env
# 编辑 .env，设置 BOT_TOKEN 和 OPENAI_API_KEY
```

## 项目架构

```
src/
├── ai/
│   └── chat.py          # AI 回复生成（OpenAI 兼容接口）
├── bot/
│   ├── handlers.py      # Bot 命令处理器
│   └── messages.py      # 消息模板
├── client/
│   └── manager.py       # Telethon 客户端管理
├── db/
│   ├── database.py      # 数据库连接与初始化
│   └── models.py        # SQLAlchemy 模型定义
└── utils/
    └── crypto.py        # Fernet 加密工具
```

## 核心模块说明

### Bot 处理器 (`src/bot/handlers.py`)

负责处理所有 Telegram Bot 命令，包括：
- 登录流程（ConversationHandler）
- 托管控制（start/stop_hosting）
- 设置管理（settings、whitelist、blacklist）
- 消息回复任务调度

关键函数：
- `_handle_incoming_message()`: 处理用户账号收到的消息，执行过滤和队列管理
- `_send_reply_task()`: 执行 AI 回复任务，支持延迟发送
- `_should_reply()`: 判断是否应该回复（黑白名单、群聊过滤）
- `_build_context()`: 构建 AI 对话上下文（最近 N 条消息，由 `CONTEXT_MAX_MESSAGES` 控制，默认 10）

并发控制：
- `MAX_CONCURRENT_REPLIES`: 最大并发回复数（默认 4）
- `MAX_CONCURRENT_REPLIES_PER_USER`: 单用户最大并发（默认自动派生，可手动覆盖）
- `MAX_PENDING_REPLY_TASKS`: 最大等待队列长度（默认 200）
- `MAX_PENDING_REPLY_TASKS_PER_USER`: 单用户最大等待队列（默认自动派生，可手动覆盖）
- `AUTO_REPLY_COOLDOWN_SECONDS`: 同一对话冷却时间（默认 15 秒）
- `INFLIGHT_COOLDOWN_TTL_SECONDS`: 内存级冷却窗口清理阈值（可选）
- `CONTEXT_MAX_MESSAGES`: 内存上下文窗口（默认 10）
- `CONTEXT_CACHE_MAX_CHATS`: 内存上下文最大对话数（默认 1000）
- `CONTEXT_TTL_SECONDS`: 内存上下文 TTL（默认 21600）

消息顺序：
- 同一用户的同一对话采用内存队列串行处理，避免回复乱序。

日志批处理：
- `ENABLE_ASYNC_LOGGING`: 启用异步日志写入（默认 1）
- `LOG_QUEUE_MAXSIZE`: 日志队列容量（默认 1000）
- `LOG_BATCH_SIZE`: 每批写入条数（默认 20）
- `LOG_BATCH_INTERVAL`: 刷盘间隔秒数（默认 1.0）

### 客户端管理 (`src/client/manager.py`)

管理 Telethon 用户客户端：
- `UserClient`: 单个用户的客户端封装，负责连接、登录、消息监听
- `ClientManager`: 全局客户端管理器，管理所有用户客户端的生命周期
- `client_manager`: 全局单例实例

关键方法：
- `connect()`: 连接并检查授权状态
- `send_code()`: 发送登录验证码
- `sign_in()`: 执行登录验证（支持两步验证）
- `start_listening()`: 开始监听新消息
- `stop()`: 停止客户端

相关环境变量：
- `CLIENT_RECONNECT_INITIAL_SECONDS`: 断线重连初始等待（默认 1 秒）
- `CLIENT_RECONNECT_MAX_SECONDS`: 断线重连最大等待（默认 30 秒）

### AI 模块 (`src/ai/chat.py`)

- 使用 OpenAI 兼容接口
- 支持上下文（最近 5 条消息）
- 支持自定义提示词
- 内置超时和重试机制

关键函数：
- `get_client()`: 获取或创建 OpenAI 客户端单例
- `generate_reply()`: 生成 AI 回复

环境变量：
- `AI_TIMEOUT_SECONDS`: 请求超时（默认 15 秒）
- `AI_MAX_RETRIES`: 最大重试次数（默认 1）

## 数据库模型

| 模型 | 表名 | 说明 |
|------|------|------|
| `User` | `users` | 用户基本信息、托管状态 |
| `UserCredential` | `user_credentials` | 加密存储的 API 凭证 |
| `UserSettings` | `user_settings` | 用户个性化设置 |
| `MessageLog` | `message_logs` | 消息回复日志 |
| `ContactList` | `contact_lists` | 白名单/黑名单 |

### 关系

```
User (1) ──── (1) UserCredential
     (1) ──── (1) UserSettings
     (1) ──── (N) MessageLog
```

## 生产相关脚本联调（开发环境建议）

为避免“开发环境看起来正常，生产上线翻车”，建议在开发阶段也执行一次运维脚本联调：

```bash
# 预检（基础）
python scripts/ready_check.py

# 预检（严格：会实际做 DB/schema 校验）
python scripts/ready_check.py --strict

# 迁移 + 备份 + 恢复演练（建议在临时测试数据库）
python scripts/migrate.py
./scripts/backup.sh /tmp/tg-auto-reply-backups
./scripts/restore.sh <db-backup> <key-backup>
```

注意事项：
- `backup.sh` 默认在数据库文件不存在时直接失败；仅首次初始化场景可临时 `BACKUP_ALLOW_MISSING_DB=1`。
- `restore.sh` 会检查实例锁，检测到服务在运行时拒绝恢复。
- 生产环境下 `ENCRYPTION_KEY/ENCRYPTION_KEY_FILE` 必须是 Fernet 兼容密钥。

## 测试

### 运行测试

```bash
# 运行所有测试（含覆盖率）
pytest

# 运行特定测试文件
pytest tests/test_handlers_helpers.py

# 显示详细输出
pytest -v
```

### 覆盖率要求

项目要求覆盖率 ≥80%（见 `pyproject.toml`）。

### 测试文件说明

| 文件 | 测试内容 |
|------|----------|
| `test_handlers_commands.py` | Bot 命令处理 |
| `test_handlers_helpers.py` | 辅助函数 |
| `test_handlers_login_flow.py` | 登录流程 |
| `test_handlers_hosting.py` | 托管控制 |
| `test_client_manager.py` | 客户端管理 |
| `test_ai_chat.py` | AI 回复生成 |
| `test_crypto.py` | 加密工具 |
| `test_db_schema.py` | 数据库模型 |
| `test_end_to_end.py` | 端到端测试 |

## 代码规范

### 格式化工具

- **Ruff**: 代码检查和格式化
- **Black**: 代码格式化（可选）

```bash
# 运行 ruff 检查
ruff check src/

# 自动修复
ruff check --fix src/
```

### 配置

见 `pyproject.toml`：
- 行长度限制：100 字符
- 目标 Python 版本：3.10

### 命名约定

- 私有函数：`_function_name()`
- 异步函数：使用 `async def`
- 常量：`UPPER_CASE`

## 扩展开发

### 添加新命令

1. 在 `src/bot/handlers.py` 添加处理函数
2. 在 `main.py` 注册 CommandHandler
3. 更新 `src/bot/messages.py` 添加消息模板

### 添加新设置项

1. 在 `src/db/models.py` 的 `UserSettings` 添加字段
2. 运行 `python scripts/migrate.py` 更新数据库
3. 在 `handlers.py` 的 `settings()` 函数添加处理逻辑

### 更换 AI 提供商

修改环境变量即可：
```env
OPENAI_BASE_URL=https://your-provider.com/v1
AI_MODEL=your-model-name
```

## 常见问题

### 数据库锁定

SQLite 不支持高并发写入，生产环境建议：
- 确保单实例运行
- 或迁移到 PostgreSQL

### 登录会话过期

Telegram 会话可能因安全原因失效，需重新 `/login`。
