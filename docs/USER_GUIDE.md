# 用户指南

本指南帮助你快速上手使用 AI 消息托管机器人。

## 准备工作

### 1. 获取 Telegram API 凭证

1. 访问 https://my.telegram.org
2. 使用手机号登录
3. 点击 "API development tools"
4. 创建应用，记录 `api_id` 和 `api_hash`

### 2. 找到你的机器人

在 Telegram 中搜索机器人用户名，或通过管理员提供的链接访问。

## 基本使用流程

### 第一步：登录

1. 发送 `/login`
2. 按提示依次输入：
   - API ID（纯数字）
   - API Hash（32位字符串）
   - 手机号（如 `+8613800138000`）
   - 验证码（发送到你的 Telegram）
   - 两步验证密码（如已开启）

### 第二步：配置设置

发送 `/settings` 查看当前设置，可调整：

```
/settings ai on       # 开启 AI 回复
/settings delay 5     # 回复延迟 5 秒
/settings groups off  # 不回复群聊
```

### 第三步：开始托管

发送 `/start_hosting` 开始自动回复。

### 第四步：查看日志

发送 `/logs` 查看最近的回复记录。

## 常用命令速查

| 命令 | 作用 |
|------|------|
| `/start` | 显示欢迎信息 |
| `/help` | 查看所有命令 |
| `/login` | 登录账号 |
| `/logout` | 退出并清除数据 |
| `/status` | 查看当前状态 |
| `/start_hosting` | 开始托管 |
| `/stop_hosting` | 停止托管 |
| `/logs` | 查看回复日志 |
| `/stats` | 查看统计 |
| `/settings` | 管理设置 |

## 高级功能

### 自定义 AI 提示词

```
/set_prompt 你是我的私人助理，用简洁友好的语气回复
```

### 白名单模式

只回复特定联系人：

```
/settings whitelist_only on
/whitelist add 123456
/whitelist add @username
```

### 黑名单过滤

屏蔽特定联系人：

```
/settings blacklist on
/blacklist add 123456
```

## 常见问题

### 为什么机器人不回复我的消息？

Bot 只响应命令（以 `/` 开头），普通文本不会触发回复。

### 登录失败怎么办？

- 检查 API ID/Hash 是否正确
- 确认手机号格式（需含国家代码如 `+86`）
- 如有两步验证，需输入密码

### 如何停止自动回复？

发送 `/stop_hosting` 即可停止。

### 数据安全吗？

- 凭证使用 Fernet 加密存储
- Session 数据仅保存在本地
- 随时可 `/logout` 清除所有数据

## 安全提示

- 不要在公共场合输入敏感信息
- 定期检查 `/logs` 确认回复内容
- 如发现异常，立即 `/stop_hosting` 并 `/logout`
