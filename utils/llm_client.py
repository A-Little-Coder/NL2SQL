# ============================================================================
# LLMClient — 基于 LangChain ChatOpenAI 的薄封装（OpenSpec migrate-llm-client-to-langchain）
# ============================================================================
# 功能说明：
#   对外暴露 LangChain Runnable 风格的接口（invoke / stream / ainvoke / astream），
#   内部用 langchain_openai.ChatOpenAI 调用 Qwen API（DashScope 兼容接口）。
#
#   关键技术要点：
#   - ChatOpenAI 必须传 `output_version="responses/v1"`，否则 Qwen 的 reasoning_content
#     字段会被 LangChain 默认丢弃（见 design.md 决策 8）
#   - 在 responses/v1 模式下 chunk.content 是 list[dict]，按 type 区分：
#       {"type": "reasoning", "summary": [{"text": "...", ...}]}
#       {"type": "text",      "text": "..."}
#   - enable_thinking 通过 `extra_body` 顶层参数（不是 model_kwargs）透传
#   - 中文思考指令在入口集中注入（用 SystemMessage 对象操作，决策 7）
#
# 公开 API：
#   class LLMClient
#     ─ invoke(messages, *, as_json=False, temperature=None, max_tokens=None)
#     ─ stream(messages, *, as_json=False, temperature=None, max_tokens=None)
#     ─ ainvoke(messages, *, ...) / astream(messages, *, ...)
#
#   模块级辅助函数（业务侧自由组合）：
#     ─ accumulate(stream_iter) -> str
#     ─ stream_with_sse(stream_iter) -> str
#     ─ parse_json(text) -> Dict[str, Any]
# ============================================================================


import json
import os
import re
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Tuple, Union

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.prompt_values import PromptValue
from langchain_openai import ChatOpenAI
from loguru import logger

# 延迟导入 streaming 模块（用于自动 SSE 推送），仅在 stream_with_sse 中需要
try:
    from src.api.streaming import current_emitter, current_node
    _HAS_STREAMING = True
except Exception:
    _HAS_STREAMING = False
    current_emitter = None  # type: ignore
    current_node = None  # type: ignore


# ──────────────────────────────────────────────────────────────────────
# 配置常量
# ──────────────────────────────────────────────────────────────────────

_ENABLE_THINKING_DEFAULT = os.getenv("LLM_ENABLE_THINKING", "true").lower() in (
    "1", "true", "yes",
)

# 决策 7：中文思考指令注入，让 Qwen3 reasoning 输出中文
_CHINESE_THINKING_DEFAULT = os.getenv("LLM_CHINESE_THINKING", "true").lower() in (
    "1", "true", "yes",
)
_CHINESE_THINKING_INSTRUCTION = "请全程使用中文进行内部思考和推理。"


# ──────────────────────────────────────────────────────────────────────
# LLMClient
# ──────────────────────────────────────────────────────────────────────

class LLMClient:
    """
    基于 LangChain ChatOpenAI 的 LLM 客户端封装

    设计理念（决策 1-12）：
    - 公开 API 用 LangChain Runnable 风格：invoke / stream / ainvoke / astream
    - 入参要求 List[BaseMessage] 或 PromptValue（不接受 dict）
    - 流式 yield (content_chunk, reasoning_chunk) 二元组
    - SSE 推送、文本累积、JSON 解析全部由业务侧用 accumulate / stream_with_sse /
      parse_json 三个辅助函数完成
    - reasoning 仅依赖 output_version="responses/v1" 模式下的 chunk.content list 结构

    用法：
    ```python
    from langchain_core.messages import SystemMessage, HumanMessage
    from utils.llm_client import LLMClient, accumulate, stream_with_sse, parse_json

    llm = LLMClient()
    msgs = [SystemMessage("你是助手"), HumanMessage("你好")]

    # 同步阻塞（CLI / 离线）
    text = llm.invoke(msgs)
    data = llm.invoke(msgs, as_json=True)

    # 流式 + SSE 推送（API 节点）
    text = stream_with_sse(llm.stream(msgs))
    data = parse_json(stream_with_sse(llm.stream(msgs, as_json=True)))

    # 异步
    text = await llm.ainvoke(msgs)
    text = accumulate([c async for c in llm.astream(msgs)])
    ```
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        enable_thinking: Optional[bool] = None,
    ):
        """
        初始化 LLM 客户端

        Args:
            model: 模型名称，默认从 QWEN_MODEL 读取
            api_key: API Key，默认从 QWEN_API_KEY 读取
            base_url: API 基础 URL，默认 DashScope 兼容接口
            temperature: 默认采样温度
            max_tokens: 默认生成上限
            enable_thinking: 是否开启 Qwen3 思考链（默认从 LLM_ENABLE_THINKING 读）

        注意：
            - ChatOpenAI 实例存于 self._chat_model（私有），业务侧不应直接访问
            - 不再暴露 self.client（裸 OpenAI 客户端别名已删除）
        """
        self.model = model or os.getenv("QWEN_MODEL", "qwen3.6-plus-2026-04-02")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.enable_thinking = (
            _ENABLE_THINKING_DEFAULT if enable_thinking is None else bool(enable_thinking)
        )

        key = api_key or os.getenv("QWEN_API_KEY", "")
        url = base_url or os.getenv(
            "QWEN_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        if not key:
            raise ValueError("请设置 QWEN_API_KEY 环境变量或传入 api_key 参数")

        # 关键：output_version="responses/v1" 让 chunk.content 输出 list[dict]，
        # 否则 Qwen 的 reasoning_content 字段会被丢弃
        self._chat_model = ChatOpenAI(
            model=self.model,
            api_key=key,
            base_url=url,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            output_version="responses/v1",
            extra_body=self._build_extra_body(),
        )

        logger.info(
            f"LLMClient 初始化完成: model={self.model}, "
            f"enable_thinking={self.enable_thinking}, "
            f"output_version=responses/v1"
        )

    # ── 公开 API：同步 ────────────────────────────────────────────────

    def invoke(
        self,
        messages: Union[List[BaseMessage], PromptValue],
        *,
        as_json: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        thinking: Optional[bool] = None,
        run_name: Optional[str] = None,
    ) -> Union[str, Dict[str, Any]]:
        """
        同步阻塞调用 ChatOpenAI.invoke

        Args:
            messages: List[BaseMessage] 或 PromptValue（来自 ChatPromptTemplate.format_prompt）
            as_json: True 时绑定 response_format={"type":"json_object"}，返回 dict
            temperature / max_tokens: 按调用覆盖采样参数
            thinking: 按调用覆盖 enable_thinking（None=沿用构造时默认值）
            run_name: LangSmith 上该次 LLM 调用的 span 名（如 "cache-check" / "ir-keywords"）；
                      None 时使用 LangChain 默认（"ChatOpenAI"）

        Returns:
            as_json=False → 完整正文字符串
            as_json=True  → 解析后的 dict（JSON 失败时返回 {"raw_response": text}）

        Raises:
            TypeError: 传入了 dict 形式的 messages
        """
        lc_messages = self._prepare_lc_messages(messages)
        bound = self._bind_runtime(temperature, max_tokens, as_json, thinking, run_name)

        ai_msg: AIMessage = bound.invoke(lc_messages)
        text = self._extract_text_from_message(ai_msg)
        return parse_json(text) if as_json else text

    def stream(
        self,
        messages: Union[List[BaseMessage], PromptValue],
        *,
        as_json: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        thinking: Optional[bool] = None,
        run_name: Optional[str] = None,
    ) -> Iterator[Tuple[Optional[str], Optional[str]]]:
        """
        流式调用 ChatOpenAI.stream

        Args:
            thinking: 按调用覆盖 enable_thinking（None=沿用构造时默认值）
            run_name: LangSmith 上该次 LLM 调用的 span 名

        Yields:
            (content_chunk, reasoning_chunk) 二元组
            - content_chunk: 正文片段，无则 None
            - reasoning_chunk: 思考链片段，无则 None
            - 同一 chunk 可能同时含两者，业务侧自行决定累积/推 SSE

        注意：
            as_json=True 时仅约束 LLM 输出格式（response_format=json_object），
            **不解析 JSON**——解析由业务侧调 parse_json(accumulate(stream)) 完成
        """
        lc_messages = self._prepare_lc_messages(messages)
        bound = self._bind_runtime(temperature, max_tokens, as_json, thinking, run_name)

        for chunk in bound.stream(lc_messages):
            text_part, reasoning_part = self._extract_chunk_blocks(chunk)
            yield (text_part, reasoning_part)

    # ── 公开 API：异步 ────────────────────────────────────────────────

    async def ainvoke(
        self,
        messages: Union[List[BaseMessage], PromptValue],
        *,
        as_json: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        thinking: Optional[bool] = None,
        run_name: Optional[str] = None,
    ) -> Union[str, Dict[str, Any]]:
        """异步版 invoke，签名一致"""
        lc_messages = self._prepare_lc_messages(messages)
        bound = self._bind_runtime(temperature, max_tokens, as_json, thinking, run_name)

        ai_msg: AIMessage = await bound.ainvoke(lc_messages)
        text = self._extract_text_from_message(ai_msg)
        return parse_json(text) if as_json else text

    async def astream(
        self,
        messages: Union[List[BaseMessage], PromptValue],
        *,
        as_json: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        thinking: Optional[bool] = None,
        run_name: Optional[str] = None,
    ) -> AsyncIterator[Tuple[Optional[str], Optional[str]]]:
        """异步版 stream，yield 与同步 stream 相同的二元组"""
        lc_messages = self._prepare_lc_messages(messages)
        bound = self._bind_runtime(temperature, max_tokens, as_json, thinking, run_name)

        async for chunk in bound.astream(lc_messages):
            text_part, reasoning_part = self._extract_chunk_blocks(chunk)
            yield (text_part, reasoning_part)

    # ── 私有：消息准备 ────────────────────────────────────────────────

    def _prepare_lc_messages(
        self,
        messages: Union[List[BaseMessage], PromptValue],
    ) -> List[BaseMessage]:
        """
        标准化入参 + 中文思考注入

        - PromptValue → to_messages()
        - List[BaseMessage] 原样
        - List[Dict] → 抛 TypeError（业务侧必须用 BaseMessage）

        然后调用 _inject_chinese_thinking 给消息列表追加/合并中文指令。
        """
        normalized = self._normalize_messages(messages)
        return self._inject_chinese_thinking(normalized)

    @staticmethod
    def _normalize_messages(
        messages: Union[List[BaseMessage], PromptValue],
    ) -> List[BaseMessage]:
        """把入参统一转成 List[BaseMessage]"""
        if isinstance(messages, PromptValue):
            return list(messages.to_messages())
        if not isinstance(messages, list):
            raise TypeError(
                f"messages 必须是 List[BaseMessage] 或 PromptValue，实际为 {type(messages).__name__}"
            )
        # 检查每个元素都是 BaseMessage（拦截 dict 入参）
        for i, m in enumerate(messages):
            if not isinstance(m, BaseMessage):
                raise TypeError(
                    f"messages[{i}] 必须是 BaseMessage（SystemMessage / HumanMessage 等），"
                    f"实际为 {type(m).__name__}。请用 langchain_core.messages 构造。"
                )
        return list(messages)

    def _inject_chinese_thinking(
        self,
        messages: List[BaseMessage],
    ) -> List[BaseMessage]:
        """决策 7：在 LLMClient 入口统一注入中文思考指令"""
        if not _CHINESE_THINKING_DEFAULT:
            return messages
        if not messages:
            return [SystemMessage(_CHINESE_THINKING_INSTRUCTION)]

        first = messages[0]
        if isinstance(first, SystemMessage):
            # 取 content 字段——SystemMessage.content 可能是 str 或 list[dict]
            original = first.content if isinstance(first.content, str) else str(first.content)
            merged = original.rstrip() + "\n\n" + _CHINESE_THINKING_INSTRUCTION
            return [SystemMessage(merged)] + list(messages[1:])

        return [SystemMessage(_CHINESE_THINKING_INSTRUCTION)] + list(messages)

    # ── 私有：参数绑定 ────────────────────────────────────────────────

    def _bind_runtime(
        self,
        temperature: Optional[float],
        max_tokens: Optional[int],
        as_json: bool,
        thinking: Optional[bool] = None,
        run_name: Optional[str] = None,
    ):
        """构造 bind() 参数，返回绑定后的 Runnable

        thinking=None 时沿用构造时 enable_thinking；
        thinking=True/False 时通过 bind(extra_body=...) 覆盖。

        run_name 用于 LangSmith 上该次 LLM 调用的 span 名（§7a 决策）：
        - None：沿用 LangChain 默认（"ChatOpenAI"）
        - 给值：调用 with_config(run_name=...) 标注该次调用
        - 注意顺序：先 with_config（runtime 配置）再 bind（model 参数），
          避免 RunnableBinding 嵌套语义混乱。
        """
        kw: Dict[str, Any] = {}
        if temperature is not None:
            kw["temperature"] = temperature
        if max_tokens is not None:
            kw["max_tokens"] = max_tokens
        if as_json:
            kw["response_format"] = {"type": "json_object"}
        if thinking is not None:
            kw["extra_body"] = {"enable_thinking": bool(thinking)}

        base = self._chat_model
        if run_name is not None:
            base = base.with_config(run_name=run_name)
        return base.bind(**kw) if kw else base

    def _build_extra_body(self) -> Dict[str, Any]:
        """构造 ChatOpenAI 的 extra_body 顶层参数"""
        extra_body: Dict[str, Any] = {}
        # 显式传 enable_thinking（开/关都传，与 Qwen API 行为对齐）
        extra_body["enable_thinking"] = bool(self.enable_thinking)
        return extra_body

    # ── 私有：chunk / message 解析（output_version=responses/v1） ─────

    @staticmethod
    def _extract_chunk_blocks(chunk) -> Tuple[Optional[str], Optional[str]]:
        """
        解析 stream chunk 的 content 字段（list[dict] 结构）

        Returns:
            (text_chunk, reasoning_chunk)
            - 任一为空则返回 None
            - 同一 chunk 可能同时含两者
        """
        content = chunk.content
        if not isinstance(content, list):
            # 非 responses/v1 模式的兜底（理论不应出现）
            return (content or None, None)

        text_buf = ""
        reasoning_buf = ""
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "reasoning":
                # reasoning block 的 summary 是 list[dict{text}]
                for s in block.get("summary", []):
                    if isinstance(s, dict) and s.get("text"):
                        reasoning_buf += s["text"]
            elif block_type == "text":
                text_buf += block.get("text", "")

        return (text_buf or None, reasoning_buf or None)

    @staticmethod
    def _extract_text_from_message(message: AIMessage) -> str:
        """
        解析阻塞 invoke 返回的 AIMessage.content

        在 output_version=responses/v1 模式下 content 是 list[dict]，
        我们只关心 text block 的文本（reasoning 在阻塞调用中不暴露）
        """
        content = message.content
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text_buf = ""
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_buf += block.get("text", "")
            return text_buf.strip()
        return str(content).strip() if content else ""


# ══════════════════════════════════════════════════════════════════════
# 模块级辅助函数（业务侧自由组合）
# ══════════════════════════════════════════════════════════════════════


def accumulate(
    stream_iter: Iterator[Tuple[Optional[str], Optional[str]]],
) -> str:
    """
    累积 LLMClient.stream() 的 yield 元组流为完整正文字符串

    思考链片段被丢弃。

    Args:
        stream_iter: LLMClient.stream() 返回的迭代器

    Returns:
        完整正文字符串
    """
    parts: List[str] = []
    for content_chunk, _reasoning_chunk in stream_iter:
        if content_chunk:
            parts.append(content_chunk)
    return "".join(parts)


def stream_with_sse(
    stream_iter: Iterator[Tuple[Optional[str], Optional[str]]],
) -> str:
    """
    累积正文 + 自动把思考链片段推 SSE llm_thinking 事件

    依赖 src.api.streaming.current_emitter ContextVar。
    无 emitter 时静默累积（CLI / 测试场景）。

    SSE 事件结构：
        {"node": <current_node 或 "unknown">, "text": <reasoning chunk>}

    Args:
        stream_iter: LLMClient.stream() 返回的迭代器

    Returns:
        完整正文字符串
    """
    emitter = current_emitter.get() if _HAS_STREAMING else None
    node_name = (current_node.get() if _HAS_STREAMING else None) or "unknown"

    parts: List[str] = []
    for content_chunk, reasoning_chunk in stream_iter:
        if content_chunk:
            parts.append(content_chunk)
        if reasoning_chunk and emitter is not None:
            try:
                emitter.emit("llm_thinking", {"node": node_name, "text": reasoning_chunk})
            except Exception as e:
                # 推 SSE 失败不影响主流程
                logger.warning(f"SSE llm_thinking 推送失败: {e}")
    return "".join(parts)


def parse_json(text: str) -> Dict[str, Any]:
    """
    解析 LLM 返回的 JSON 字符串

    解析失败时兜底正则匹配第一个 {...} 块；
    最终失败返回 {"raw_response": text}（兼容现有业务对失败的处理）

    Args:
        text: LLM 完整输出文本

    Returns:
        解析后的 dict
    """
    if not text:
        return {"raw_response": text}

    # 直接 JSON 解析
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # 兜底：正则匹配首个 {...} 块
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    logger.warning(f"无法解析 JSON 响应: {text[:200]}")
    return {"raw_response": text}
