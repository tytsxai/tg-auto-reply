"""Bot 消息文本 - 增强信任感的友好文案"""

WELCOME = """
👋 欢迎使用消息托管助手！

我可以帮你自动回复 Telegram 消息，让你在忙碌时也不会错过重要对话。

🔐 安全承诺：
• 凭证使用 Fernet 加密存储
• 所有操作都有透明日志可查
• 随时可以一键停止托管

📋 使用流程：
1. /login - 登录账号
2. /settings - 配置规则
3. /start_hosting - 开始托管

输入 /help 查看所有命令
"""

HELP = """
📖 命令列表

账号管理：
/login - 登录 Telegram 账号
/logout - 退出登录并清除数据
/status - 查看当前状态

托管控制：
/start_hosting - 开始自动回复
/stop_hosting - 停止自动回复

设置：
/settings - 查看/修改设置（示例：/settings ai on）
/set_prompt - 设置 AI 回复提示词
/whitelist - 管理白名单（list/add/remove/clear）
/blacklist - 管理黑名单（list/add/remove/clear）

透明度：
/logs - 查看最近的回复日志
/stats - 查看统计数据

其他：
/help - 显示此帮助
/about - 关于本机器人
"""

LOGIN_START = """
🔑 开始登录流程

为了托管你的消息，我需要连接到你的 Telegram 账号。

⚠️ 重要说明：
• 需要你的 API ID 和 API Hash（从 my.telegram.org 获取）
• 这些凭证仅用于消息监听，不会被滥用
• 我会尝试自动删除你发送的敏感信息，如仍可见请手动删除
• 所有数据加密存储，你可以随时删除

准备好了吗？请发送你的 API ID（纯数字）：
"""

LOGIN_API_HASH = """
✅ API ID 已收到

请发送你的 API Hash（32位字符串）：
"""

LOGIN_PHONE = """
✅ API Hash 已收到

请发送你的手机号码（包含国家代码，如 +86...）：
"""

LOGIN_CODE = """
📱 验证码已发送到你的 Telegram！

请输入收到的验证码：

💡 提示：验证码可能在其他设备的 Telegram 中显示
"""

LOGIN_2FA = """
🔐 检测到两步验证

请输入你的两步验证密码：
"""

LOGIN_SUCCESS = """
🎉 登录成功！

你的账号已连接。现在可以：
• /start_hosting - 开始自动回复
• /settings - 配置回复规则

你的数据安全是我们的首要任务。
"""

LOGIN_FAILED = """
❌ 登录失败

原因：{reason}

请检查后重试 /login
"""

HOSTING_STARTED = """
✅ 托管已启动

我现在会自动回复你收到的消息。

📊 实时状态：
• 监听中...
• 回复延迟：{delay}秒
• AI 模式：{ai_mode}

使用 /logs 随时查看回复记录
使用 /stop_hosting 停止托管
"""

HOSTING_STOPPED = """
⏹️ 托管已停止

不再自动回复消息。你的账号仍保持登录状态。

使用 /start_hosting 重新开始
使用 /logout 完全退出
"""

LOG_ENTRY = """
📝 [{time}]
来自：{sender}
原消息：{message}
AI回复：{reply}
状态：{status}
"""
