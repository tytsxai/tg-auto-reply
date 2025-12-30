# API 文档

本项目主要通过 Telegram Bot 命令交互，同时提供可选的 HTTP 监控端点。

## Telegram 命令

| 命令 | 说明 |
| --- | --- |
| `/start` | 开始使用 |
| `/help` | 帮助信息 |
| `/login` | 登录 Telegram 账号 |
| `/logout` | 退出登录并清除数据 |
| `/status` | 查看当前状态 |
| `/start_hosting` | 开始自动回复 |
| `/stop_hosting` | 停止自动回复 |
| `/logs` | 查看最近 5 条回复日志 |
| `/stats` | 查看统计信息 |
| `/settings` | 查看/修改设置 |
| `/set_prompt` | 设置自定义提示词 |
| `/whitelist` | 管理白名单 |
| `/blacklist` | 管理黑名单 |
| `/about` | 关于本机器人 |
| `/cancel` | 取消当前操作 |

### /settings 子命令

- `/settings ai on|off` 启用/关闭 AI 回复
- `/settings delay <秒>` 设置回复延迟
- `/settings groups on|off` 是否回复群聊
- `/settings whitelist_only on|off` 仅白名单回复
- `/settings blacklist on|off` 黑名单过滤

## HTTP 监控端点（可选）

通过设置 `HEALTHCHECK_PORT` 或 `ENABLE_HTTP_HEALTHCHECK=1` 启用。

| 端点 | 说明 |
| --- | --- |
| `GET /healthz` | 存活探针 |
| `GET /readyz` | 就绪探针（含 DB & schema 检查） |
| `GET /metrics` | Prometheus 风格指标 |

如设置 `HEALTHCHECK_TOKEN`，则需要在请求中添加：

- `X-Health-Token: <token>`，或
- `Authorization: Bearer <token>`
