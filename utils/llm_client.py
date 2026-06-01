# ============================================================================
# LLM 客户端封装
# ============================================================================
# 功能说明:
#   统一封装 Qwen API 调用，提供简洁的 chat 接口
#   支持流式和非流式输出
# ============================================================================


import os
import json
from typing import List, Dict, Any, Optional

from openai import OpenAI
from loguru import logger


class LLMClient:
    """
    LLM 客户端 - 基于 Qwen API（兼容 OpenAI 接口）

    从 .env 文件读取 QWEN_API_KEY 和 QWEN_BASE_URL。

    Attributes:
        client: OpenAI 兼容客户端
        model: 模型名称
    """

    def __init__(
        self,
        model: str = None,
        api_key: str = None,
        base_url: str = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ):
        """
        初始化 LLM 客户端

        Args:
            model: 模型名称
            api_key: API Key，默认从环境变量 QWEN_API_KEY 读取
            base_url: API 基础 URL，默认从环境变量 QWEN_BASE_URL 读取
            temperature: 温度参数
            max_tokens: 最大生成 token 数
        """
        self.model = model or os.getenv("QWEN_MODEL", "qwen3.6-plus-2026-04-02")
        self.temperature = temperature
        self.max_tokens = max_tokens

        key = api_key or os.getenv("QWEN_API_KEY", "")
        url = base_url or os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

        if not key:
            raise ValueError("请设置 QWEN_API_KEY 环境变量或传入 api_key 参数")

        self.client = OpenAI(api_key=key, base_url=url)
        logger.info(f"LLMClient 初始化完成: model={self.model}")

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
        response_format: Optional[Dict] = None,
    ) -> str:
        """
        发送聊天请求

        Args:
            messages: 消息列表，格式同 OpenAI API
            temperature: 温度参数（覆盖默认值）
            max_tokens: 最大 token 数（覆盖默认值）
            response_format: 响应格式，如 {"type": "json_object"}

        Returns:
            str: 模型生成的文本
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature or self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format

        try:
            response = self.client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content
            return content.strip() if content else ""
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            raise

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
    ) -> Dict[str, Any]:
        """
        发送聊天请求并解析 JSON 响应

        Args:
            messages: 消息列表
            temperature: 温度参数

        Returns:
            Dict: 解析后的 JSON 字典
        """
        text = self.chat(
            messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试从文本中提取 JSON
            import re
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                return json.loads(match.group())
            logger.warning(f"无法解析 JSON 响应: {text[:200]}")
            return {"raw_response": text}
