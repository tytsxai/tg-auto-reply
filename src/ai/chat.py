"""AI 自动回复模块 - 基于 OpenAI 兼容接口生成智能回复。

支持自定义提示词、上下文对话、超时重试机制。
"""

import asyncio
import logging
import os
import re
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)
_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    """获取或创建 OpenAI 客户端单例。

    Raises:
        ValueError: 未配置 API Key
    """
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY")
        if not api_key:
            raise ValueError("未配置 OPENAI_API_KEY (或 API_KEY)")
        base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("API_BASE_URL")
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        _client = AsyncOpenAI(**kwargs)
    return _client


async def generate_reply(
    message: str,
    sender_name: str,
    context: list[dict[str, str]] | None = None,
    system_prompt: str | None = None,
) -> str:
    """生成 AI 回复。

    Args:
        message: 用户消息内容
        sender_name: 发送者名称
        context: 历史对话上下文（最多使用最近 5 条）
        system_prompt: 自定义系统提示词

    Returns:
        str: AI 生成的回复内容

    Raises:
        Exception: AI 请求失败且重试次数用尽
    """

    default_prompt = """你是用户的智能助手，帮助自动回复消息。
规则：
1. 保持友好、自然的语气
2. 回复简洁，不要过长
3. 如果不确定如何回复，礼貌地表示稍后回复
4. 不要透露你是 AI"""

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt or default_prompt}]

    # 添加上下文
    if context:
        messages.extend(context[-5:])  # 最近5条

    safe_sender = (sender_name or "未知").strip() or "未知"
    messages.append({"role": "user", "content": f"[来自 {safe_sender}]: {message}"})

    timeout_seconds = float(os.getenv("AI_TIMEOUT_SECONDS", "15"))
    if timeout_seconds <= 0:
        timeout_seconds = 15.0
    max_retries = max(0, int(os.getenv("AI_MAX_RETRIES", "1")))

    for attempt in range(max_retries + 1):
        try:
            response = await asyncio.wait_for(
                get_client().chat.completions.create(
                    model=os.getenv("AI_MODEL", "deepseek-ai/DeepSeek-V3.2"),
                    messages=messages,
                    max_tokens=200,
                    temperature=0.7,
                ),
                timeout=timeout_seconds,
            )
            reply = response.choices[0].message.content or "抱歉，我稍后回复您。"
            # 过滤模型思考过程标签（如 <think>...</think>）
            reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.DOTALL).strip()
            return reply or "抱歉，我稍后回复您。"
        except Exception as exc:
            if attempt >= max_retries:
                raise
            logger.warning("AI 请求失败，准备重试(%s/%s): %s", attempt + 1, max_retries + 1, exc)
            await asyncio.sleep(min(2 ** attempt, 5))
