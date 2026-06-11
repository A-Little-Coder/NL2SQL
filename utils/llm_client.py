# ============================================================================
# LLM 客户端封装（决策 50 增强：思考链流式推送）
# ============================================================================
# 功能说明:
#   统一封装 Qwen API 调用，提供简洁的 chat 接口
#   - 默认阻塞调用（向后兼容 CLI / 测试 / 离线脚本）
#   - 当 src.api.streaming.current_emitter 存在时，自动切换流式：
#       * qwen3 reasoning_content（思考链）→ 实时推送 llm_thinking 事件
#       * 正文 delta.content（JSON 片段）→ 仅累积，不推送
#   - 拿到完整正文后照常 json.loads（chat_json）或 strip（chat）
# ============================================================================


import os
import json
import re
from typing import Any, Callable, Dict, List, Optional

from openai import OpenAI
from loguru import logger


# 延迟导入避免循环依赖（streaming 不依赖 llm_client，但 llm_client 用到 contextvar）
try:
    from src.api.streaming import current_emitter, current_node
    _HAS_STREAMING = True
except Exception:
    _HAS_STREAMING = False
    current_emitter = None  # type: ignore
    current_node = None  # type: ignore


_ENABLE_THINKING_DEFAULT = os.getenv("LLM_ENABLE_THINKING", "true").lower() in (
    "1", "true", "yes",
)

# 决策 51：中文思考指令注入，统一让 Qwen3 reasoning_content 输出中文
_CHINESE_THINKING_DEFAULT = os.getenv("LLM_CHINESE_THINKING", "true").lower() in (
    "1", "true", "yes",
)
_CHINESE_THINKING_INSTRUCTION = "请全程使用中文进行内部思考和推理。"


def _inject_chinese_thinking(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """在 messages 头部注入中文思考指令（决策 51）

    规则：
    - 环境变量 LLM_CHINESE_THINKING=false 时不注入
    - 首条为 system role：追加到该消息内容末尾（保留原 prompt 完整性）
    - 首条不是 system role：在列表开头插入新的 system 消息
    - 返回新列表，不修改原 messages

    Args:
        messages: 原 messages 列表

    Returns:
        注入后的新 messages 列表（若关闭则原样返回）
    """
    if not _CHINESE_THINKING_DEFAULT:
        return messages
    if not messages:
        return [{"role": "system", "content": _CHINESE_THINKING_INSTRUCTION}]

    new_messages = list(messages)
    first = new_messages[0]
    if first.get("role") == "system":
        # 追加到既有 system 末尾
        original = first.get("content", "")
        merged = original.rstrip() + "\n\n" + _CHINESE_THINKING_INSTRUCTION
        new_messages[0] = {**first, "content": merged}
    else:
        # 插入新 system 消息
        new_messages.insert(
            0, {"role": "system", "content": _CHINESE_THINKING_INSTRUCTION}
        )
    return new_messages


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
        enable_thinking: bool = None,
    ):
        """
        初始化 LLM 客户端

        Args:
            model: 模型名称
            api_key: API Key，默认从环境变量 QWEN_API_KEY 读取
            base_url: API 基础 URL，默认从环境变量 QWEN_BASE_URL 读取
            temperature: 温度参数
            max_tokens: 最大生成 token 数
            enable_thinking: 是否开启 qwen3 思考链（默认从 LLM_ENABLE_THINKING 读）
        """
        self.model = model or os.getenv("QWEN_MODEL", "qwen3.6-plus-2026-04-02")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.enable_thinking = (
            _ENABLE_THINKING_DEFAULT if enable_thinking is None else bool(enable_thinking)
        )

        key = api_key or os.getenv("QWEN_API_KEY", "")
        url = base_url or os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

        if not key:
            raise ValueError("请设置 QWEN_API_KEY 环境变量或传入 api_key 参数")

        self.client = OpenAI(api_key=key, base_url=url)
        logger.info(
            f"LLMClient 初始化完成: model={self.model}, "
            f"enable_thinking={self.enable_thinking}"
        )

    # ── 公共 API（向后兼容） ────────────────────────────────────

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
        response_format: Optional[Dict] = None,
    ) -> str:
        """
        发送聊天请求（向后兼容签名）

        - 无 current_emitter（CLI / 测试）：阻塞调用，返回完整文本
        - 有 current_emitter（API SSE 场景）：流式调用，
          思考链实时推送 llm_thinking 事件，正文累积后返回
        """
        if not self._has_emitter():
            return self._chat_blocking(messages, temperature, max_tokens, response_format)

        node_name = (current_node.get() if _HAS_STREAMING else None) or "unknown"
        emitter = current_emitter.get()

        text = self.chat_stream(
            messages,
            on_thinking=lambda c: emitter.emit(
                "llm_thinking", {"node": node_name, "text": c}
            ),
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        return text.strip() if text else ""

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
    ) -> Dict[str, Any]:
        """
        发送聊天请求并解析 JSON 响应

        - 无 current_emitter：旧阻塞路径
        - 有 current_emitter：流式拿到完整 JSON 字符串后再 parse
        """
        text = self.chat(
            messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        return self._parse_json(text)

    # ── 新增：流式调用 ──────────────────────────────────────────

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        on_thinking: Optional[Callable[[str], None]] = None,
        temperature: float = None,
        max_tokens: int = None,
        response_format: Optional[Dict] = None,
    ) -> str:
        """
        流式调用 LLM（决策 50）

        - 正文 `delta.content`：仅累积到 full_text，不回调（避免推 JSON 片段）
        - 思考链 `delta.reasoning_content`：实时回调 on_thinking（自然语言可读）

        Args:
            messages: 消息列表
            on_thinking: 思考链片段回调，签名 (chunk: str) -> None
            temperature / max_tokens / response_format: 同 chat

        Returns:
            str: 完整正文文本
        """
        # 决策 51：注入中文思考指令
        messages = _inject_chinese_thinking(messages)

        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature or self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "stream": True,
        }
        if response_format:
            kwargs["response_format"] = response_format
        if self.enable_thinking:
            kwargs["extra_body"] = {"enable_thinking": True}

        full_text = ""
        try:
            stream = self.client.chat.completions.create(**kwargs)
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue

                # 思考链：自然语言，实时推送
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning and on_thinking is not None:
                    try:
                        on_thinking(reasoning)
                    except Exception as cb_err:
                        logger.warning(f"on_thinking 回调异常: {cb_err}")

                # 正文：JSON 片段，仅累积
                if delta.content:
                    full_text += delta.content
        except Exception as e:
            logger.error(f"LLM 流式调用失败: {e}")
            raise

        return full_text

    # ── 内部辅助 ────────────────────────────────────────────────

    def _has_emitter(self) -> bool:
        """检查当前线程是否在 SSE 上下文中"""
        if not _HAS_STREAMING:
            return False
        try:
            return current_emitter.get() is not None
        except Exception:
            return False

    def _chat_blocking(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
        response_format: Optional[Dict] = None,
    ) -> str:
        """旧阻塞实现路径（CLI / 测试用）"""
        # 决策 51：注入中文思考指令
        messages = _inject_chinese_thinking(messages)

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

    def _parse_json(self, text: str) -> Dict[str, Any]:
        """解析 JSON 文本，失败时尝试正则兜底"""
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            match = re.search(r"\{[\s\S]*\}", text or "")
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            logger.warning(f"无法解析 JSON 响应: {(text or '')[:200]}")
            return {"raw_response": text}
