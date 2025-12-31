# tg-auto-reply 生产运维指南

本指南面向生产部署与长期运行，尽量保持简单可执行。

## 环境变量完整参考

| 变量名 | 必填 | 默认值 | 说明 |
|--------|------|--------|------|
| `BOT_TOKEN` | 是 | - | Telegram Bot Token |
| `OPENAI_API_KEY` | 是* | - | AI API 密钥（或使用 `API_KEY`） |
| `OPENAI_BASE_URL` | 否 | OpenAI 官方 | API 基础 URL |
| `AI_MODEL` | 否 | `deepseek-ai/DeepSeek-V3.2` | 模型名称 |
| `AI_TIMEOUT_SECONDS` | 否 | `15` | AI 请求超时 |
| `AI_MAX_RETRIES` | 否 | `1` | AI 请求失败重试次数 |
| `AI_TIMEOUT_SECONDS` | 否 | `15` | AI 请求超时秒数 |
| `AI_MAX_RETRIES` | 否 | `1` | AI 请求失败重试次数 |
| `ENVIRONMENT` | 否 | `development` | 环境标识 |
| `ENCRYPTION_KEY` | 生产必填 | - | 加密密钥（Base64） |
| `ENCRYPTION_KEY_FILE` | 否 | `data/encryption.key` | 密钥文件路径 |
| `DATABASE_URL` | 否 | `sqlite+aiosqlite:///./data/bot.db` | 数据库连接 |
| `ALLOWED_TELEGRAM_IDS` | 生产必填 | - | 允许使用的用户 ID（逗号分隔） |
| `ALLOW_UNRESTRICTED_ACCESS` | 否 | - | 允许未限制访问（仅在需要时使用） |
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 |
| `LOG_FILE` | 否 | - | 日志文件路径 |
| `MAX_CONCURRENT_REPLIES` | 否 | `4` | 最大并发回复数 |
| `MAX_PENDING_REPLY_TASKS` | 否 | `200` | 最大等待队列长度 |
| `AUTO_REPLY_COOLDOWN_SECONDS` | 否 | `15` | 同一聊天回复冷却时间 |
| `SHUTDOWN_GRACE_PERIOD_SECONDS` | 否 | `10` | 退出时等待中的回复任务宽限 |
| `ENABLE_STARTUP_HEALTHCHECKS` | 否 | `1` | 启动时自检数据库 |
| `ENABLE_HTTP_HEALTHCHECK` | 否 | `0` | 启用 HTTP 健康检查 |
| `HEALTHCHECK_HOST` | 否 | `127.0.0.1` | 健康检查监听地址 |
| `HEALTHCHECK_PORT` | 否 | - | 健康检查端口 |
| `HEALTHCHECK_TOKEN` | 否 | - | 健康检查访问令牌（生产环境对外暴露必须设置） |
| `LOG_RETENTION_DAYS` | 否 | `90` | 日志保留天数 |
| `DB_BUSY_TIMEOUT_MS` | 否 | `30000` | SQLite busy_timeout |
| `DB_JOURNAL_MODE` | 否 | `WAL` | SQLite journal_mode |
| `DB_SYNCHRONOUS` | 否 | `NORMAL` | SQLite synchronous |
| `ALLOW_START_WITHOUT_AI` | 否 | - | 未配置 AI 时允许启动 |

## 必要配置

- 必须设置：`BOT_TOKEN`、`OPENAI_API_KEY`（或 `API_KEY`）
- 生产环境必须设置：`ENCRYPTION_KEY`、`ALLOWED_TELEGRAM_IDS`
- 若确实需要允许任意用户访问，设置 `ALLOW_UNRESTRICTED_ACCESS=1`
- 若希望在未配置 AI 时启动，设置 `ALLOW_START_WITHOUT_AI=1`

## 依赖锁定

建议生产环境使用锁定文件安装依赖：

```bash
pip install -r requirements.lock
pip install -e .
```
- 可选：`LOG_FILE`（启用文件日志）
- 可选：`ALLOWED_TELEGRAM_IDS`（限制控制 Bot 的用户）

## 启动与停止

启动：

```bash
source .venv/bin/activate
python main.py
```

停止：
- 发送 SIGTERM 或 Ctrl+C，程序将优雅停机并断开 Telethon 客户端

## systemd 部署（推荐）

适用于常见的 Linux 服务器场景，便于开机自启与自动重启。

示例 unit 文件（保存为 `telegram-bot.service`）：

```ini
[Unit]
Description=tg-auto-reply - Telegram AI Auto Reply Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/telegram-bot
EnvironmentFile=/opt/telegram-bot/.env
ExecStart=/opt/telegram-bot/.venv/bin/python /opt/telegram-bot/main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot
sudo systemctl start telegram-bot
sudo systemctl status telegram-bot
```

## 备份与恢复

需要备份的文件：
- `data/bot.db`（数据库）
- `data/encryption.key`（如果未使用 `ENCRYPTION_KEY` 环境变量）

备份示例（建议停止服务后执行）：

```bash
cp data/bot.db backups/bot.db.$(date +%F)
cp data/encryption.key backups/encryption.key.$(date +%F)
```

也可以使用脚本：

```bash
./scripts/backup.sh
```

可通过传参指定备份目录：

```bash
./scripts/backup.sh /opt/backups/telegram-bot
```

## 日志清理

日志表会随时间增长，建议定期清理（默认保留 90 天）：

```bash
./scripts/cleanup_logs.py 90
```

可通过环境变量 `LOG_RETENTION_DAYS` 设置默认保留天数。

## 数据库迁移

版本升级或首次部署后建议执行：

```bash
python scripts/migrate.py
```

脚本会创建表结构并初始化 schema 版本号。

## 定时任务示例

使用 cron（每天凌晨 3 点备份，4 点清理）：

```
0 3 * * * /opt/telegram-bot/scripts/backup.sh /opt/backups/telegram-bot
0 4 * * * /opt/telegram-bot/scripts/cleanup_logs.py 90
```

使用 systemd timer（仅示意）：

```
[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true
```

建议根据实际部署环境选择合适的定时方式。

恢复示例：

```bash
cp backups/bot.db.2025-01-01 data/bot.db
cp backups/encryption.key.2025-01-01 data/encryption.key
```

## 升级与回滚

升级流程建议：
1. 停止服务
2. 备份 `data/bot.db` 与密钥
3. 更新代码并重新安装依赖（如有变化）
4. 启动服务并观察日志

回滚流程建议：
1. 停止服务
2. 回滚代码到上一版本
3. 恢复备份数据库与密钥
4. 启动服务

## 运行参数建议

- `MAX_CONCURRENT_REPLIES`：限制并发回复，防止峰值压垮服务
- `MAX_PENDING_REPLY_TASKS`：限制等待队列长度，避免内存堆积
- `AUTO_REPLY_COOLDOWN_SECONDS`：同一聊天短时间内只回复一次，防止回环
- `AI_TIMEOUT_SECONDS`：AI 请求超时
- `AI_MAX_RETRIES`：AI 请求失败重试次数

## 日志轮转建议

如果使用 `LOG_FILE`，建议配置 logrotate（示例）：

```
/opt/telegram-bot/bot.log {
  daily
  rotate 7
  compress
  missingok
  notifempty
  copytruncate
}
```

如果使用 systemd，也可直接通过 `journalctl -u telegram-bot` 查看日志并配置 systemd 日志保留策略。

## 最小告警建议

建议至少关注以下日志关键词（可由日志平台或简单脚本触发告警）：
- "AI 回复失败"（上游故障或网络异常）
- "回复队列已满"（负载过高，消息被丢弃）
- "database is locked"（SQLite 并发写冲突）
- "登录已失效"（用户会话失效）

也可以使用脚本（依赖 `LOG_FILE`）：

```bash
./scripts/check_alerts.sh
```

## 定时任务安装

使用脚本生成/安装 cron（不会覆盖已有任务）：

```bash
./scripts/install_cron.sh /opt/backups/telegram-bot 90 --apply
```
## 监控与日志

- 日志建议写入 `LOG_FILE` 并用系统工具轮转
- 注意关注：AI 请求失败、回复队列溢出、数据库锁冲突
- 可启用 HTTP 健康检查：设置 `HEALTHCHECK_PORT` 或 `ENABLE_HTTP_HEALTHCHECK=1`
- 端点：`/healthz`（存活）、`/readyz`（就绪）、`/metrics`（指标）
- 如设置 `HEALTHCHECK_TOKEN`，需通过 `X-Health-Token` 或 `Authorization: Bearer` 访问

## 脚本使用说明

### backup.sh - 数据库备份

```bash
# 默认备份到 ./backups/
./scripts/backup.sh

# 指定备份目录
./scripts/backup.sh /opt/backups/telegram-bot
```

功能：
- 优先使用 `sqlite3 .backup` 命令（热备份）
- 自动备份 `encryption.key`（如未使用环境变量）
- 文件名格式：`bot.db.YYYY-MM-DD`

### migrate.py - 数据库迁移

```bash
python scripts/migrate.py
```

功能：
- 创建表结构（如不存在）
- 初始化/更新 schema 版本号

### cleanup_logs.py - 日志清理

```bash
# 清理 90 天前的日志
./scripts/cleanup_logs.py 90

# 使用环境变量默认值
./scripts/cleanup_logs.py
```

### check_alerts.sh - 告警检测

```bash
# 需要设置 LOG_FILE 环境变量
LOG_FILE=./bot.log ./scripts/check_alerts.sh
```

检测关键词（可通过 `ALERT_KEYWORDS` 自定义）：
- AI 回复失败
- 回复队列已满
- database is locked
- 登录已失效

### install_cron.sh - 定时任务安装

```bash
# 预览将添加的任务
./scripts/install_cron.sh /opt/backups 90

# 实际安装
./scripts/install_cron.sh /opt/backups 90 --apply
```

安装的任务：
- 每天 03:00 备份
- 每天 04:00 清理日志
- 每 5 分钟告警检测

## 故障排查

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| 无法解密凭证 | 密钥丢失或变更 | 恢复备份密钥或重新 `/login` |
| 登录已失效 | Telegram 会话过期 | 用户重新 `/login` |
| database is locked | 并发写入冲突 | 检查是否多实例运行 |
| AI 回复失败 | API 超时或配额用尽 | 检查网络和 API 状态 |
| 回复队列已满 | 消息量过大 | 调整 `MAX_PENDING_REPLY_TASKS` |
| 检测到已有实例在运行 | 同一 SQLite 数据库被多进程使用 | 停止重复实例后重启 |
