# API 文档

本项目主要通过 Telegram Bot 命令交互，同时提供可选的 HTTP 监控端点。

## Telegram 命令

说明：
- Bot 仅响应命令消息，普通聊天文本不会触发回复。
- 交互提示采用纯文本展示，避免 Markdown 语法符号干扰。
- 登录流程需提供 Telegram 的 API ID / API Hash。

| 命令 | 说明 |
| --- | --- |
| `/start` | 开始使用，显示欢迎信息 |
| `/help` | 显示所有可用命令 |
| `/login` | 登录 Telegram 账号（交互式流程） |
| `/logout` | 退出登录并清除本地凭证 |
| `/status` | 查看登录状态和托管状态 |
| `/start_hosting` | 开始自动回复监听 |
| `/stop_hosting` | 停止自动回复 |
| `/logs` | 查看最近 5 条回复日志 |
| `/stats` | 查看统计信息（总记录/已发送/失败） |
| `/settings` | 查看/修改设置 |
| `/set_prompt` | 设置自定义 AI 提示词 |
| `/whitelist` | 管理白名单 |
| `/blacklist` | 管理黑名单 |
| `/about` | 关于本机器人（版本信息） |
| `/cancel` | 取消当前登录流程 |

### /settings 子命令

| 子命令 | 说明 | 示例 |
| --- | --- | --- |
| `ai on\|off` | 启用/关闭 AI 回复 | `/settings ai on` |
| `delay <秒>` | 设置回复延迟（0-600秒） | `/settings delay 5` |
| `groups on\|off` | 是否回复群聊消息 | `/settings groups on` |
| `whitelist_only on\|off` | 仅回复白名单用户 | `/settings whitelist_only on` |
| `blacklist on\|off` | 启用黑名单过滤 | `/settings blacklist off` |

布尔值支持多种格式：`on/off`、`true/false`、`1/0`、`yes/no`、`开/关`

### /whitelist 和 /blacklist 子命令

| 子命令 | 说明 | 示例 |
| --- | --- | --- |
| `list` 或 `ls` | 查看列表（最近20条） | `/whitelist list` |
| `add <目标>` | 添加到列表 | `/whitelist add 123456` |
| `remove <目标>` | 从列表移除 | `/blacklist remove 123456` |
| `clear` | 清空整个列表 | `/whitelist clear` |

目标支持三种格式：
- 数字 ID：`123456` 或 `-100123456`（群组）
- 用户名：`@username`（需要已登录）
- 回复/转发消息：回复或转发目标用户的消息后执行 `add`/`remove`

### /login 登录流程

登录为交互式多步流程：

1. 发送 `/login` 开始
2. 输入 API ID（纯数字）
3. 输入 API Hash（32位字符串）
4. 输入手机号（含国家代码，如 `+8613800138000`）
5. 输入收到的验证码
6. （可选）输入两步验证密码

敏感信息会尝试自动删除，如仍可见请手动删除。

## HTTP 监控端点（可选）

通过设置 `HEALTHCHECK_PORT` 或 `ENABLE_HTTP_HEALTHCHECK=1` 启用。

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/healthz` | GET | 存活探针，返回 200 表示进程存活 |
| `/readyz` | GET | 就绪探针，检查 DB 连接、schema 版本与异步日志工作线程状态 |
| `/metrics` | GET | Prometheus 风格指标 |

### 认证

如设置 `HEALTHCHECK_TOKEN`，则需要在请求中添加：

```
X-Health-Token: <token>
```

或：

```
Authorization: Bearer <token>
```

### /readyz 返回字段（示例）

```json
{
  "status": "ok",
  "db_ok": true,
  "schema_version": 3,
  "expected_schema_version": 3,
  "async_logging_enabled": true,
  "async_log_worker_alive": true
}
```

### /metrics 指标说明

| 指标 | 类型 | 说明 |
| --- | --- | --- |
| `bot_registered_clients` | Gauge | 已注册的客户端数量 |
| `bot_running_clients` | Gauge | 正在运行的客户端数量 |
| `bot_pending_reply_tasks` | Gauge | 等待中的回复任务数 |
| `bot_active_reply_tasks` | Gauge | 活跃的回复任务数 |
| `bot_pending_reply_tasks_limit` | Gauge | 最大等待队列配置 |
| `bot_concurrent_reply_limit` | Gauge | 最大并发回复数配置 |
| `bot_pending_reply_tasks_limit_per_user` | Gauge | 单用户最大等待队列配置 |
| `bot_concurrent_reply_limit_per_user` | Gauge | 单用户最大并发回复数配置 |
| `bot_log_worker_alive` | Gauge | 异步日志工作线程状态（1=存活） |
| `bot_log_queue_size` | Gauge | 异步日志队列当前长度 |
| `bot_log_sync_fallback_total` | Counter | 队列满时回退同步写入次数 |
| `bot_log_drop_total` | Counter | 日志丢弃累计次数 |
| `bot_log_write_failure_total` | Counter | 日志写入失败累计次数 |
| `bot_ai_circuit_open` | Gauge | AI 熔断状态（1=开启） |
| `bot_ai_circuit_failures` | Gauge | AI 连续失败计数 |
| `bot_schema_version` | Gauge | 当前 schema 版本 |
| `bot_active_users` | Gauge | 托管中的活跃用户数（若统计成功） |

## 内部 API（代码级）

### AI 回复生成

```python
from src.ai import generate_reply

reply = await generate_reply(
    message="你好",
    sender_name="张三",
    context=[{"role": "user", "content": "之前的消息"}],
    system_prompt="自定义提示词"
)
```

### 客户端管理

```python
from src.client import client_manager, UserClient

# 获取客户端
client = client_manager.get_client(telegram_id)

# 检查运行状态
is_running = client_manager.is_running(telegram_id)

# 停止客户端
await client_manager.stop_client(telegram_id)

# 获取统计信息
running = client_manager.running_count()
registered = client_manager.registered_count()
```

### 回复任务管理

```python
from src.bot.handlers import (
    get_pending_reply_tasks,
    get_active_reply_task_count,
    get_reply_limits,
    wait_for_reply_tasks,
)

# 获取队列状态
pending = get_pending_reply_tasks()
active = get_active_reply_task_count()
max_concurrent, max_pending = get_reply_limits()

# 等待任务完成（用于优雅关闭）
cancelled = await wait_for_reply_tasks(timeout=10.0)
```

### 加密工具

```python
from src.utils import encryptor

# 加密敏感数据
encrypted = encryptor.encrypt("sensitive_data")

# 解密数据
decrypted = encryptor.decrypt(encrypted)
```


## 运维脚本接口（CLI）

> 以下脚本属于“运维接口”，推荐纳入发布流水线或值班手册。

| 脚本 | 用法 | 说明 |
| --- | --- | --- |
| `scripts/ready_check.py` | `python scripts/ready_check.py [--strict]` | 上线前预检；`--strict` 会做 DB/schema 真实校验 |
| `scripts/migrate.py` | `python scripts/migrate.py` | schema 迁移入口 |
| `scripts/backup.sh` | `./scripts/backup.sh [backup-dir]` | SQLite 热备份 + 密钥备份；数据库缺失默认失败 |
| `scripts/restore.sh` | `./scripts/restore.sh <db-backup> [key-backup]` | 恢复并校验；恢复前快照；实例锁占用时拒绝执行 |
| `scripts/check_alerts.sh` | `./scripts/check_alerts.sh` | 关键日志关键词巡检 |
