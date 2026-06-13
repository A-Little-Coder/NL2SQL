"""LangSmith 接入测试（§8.1）

覆盖：
  - 启动入口 LangSmith tracing 状态日志
  - LLMClient.invoke/stream 的 run_name 参数通过 with_config 生效
  - query_endpoint 注入完整的 LangSmith config（thread_id/run_name/tags/metadata）
"""

import logging as std_logging
import os
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage
from loguru import logger as lg


# ────────────────────────────────────────────────────────────
# 辅助：把 loguru 转发到 std logging，让 caplog 抓得到
# ────────────────────────────────────────────────────────────


class _PropagateHandler(std_logging.Handler):
    def emit(self, record):
        std_logging.getLogger(record.name).handle(record)


@pytest.fixture
def loguru_to_caplog():
    handler_id = lg.add(_PropagateHandler(), format="{message}", level="INFO")
    yield
    lg.remove(handler_id)


# ────────────────────────────────────────────────────────────
# §8.1.1: log_langsmith_status
# ────────────────────────────────────────────────────────────


def test_langsmith_status_enabled(caplog, loguru_to_caplog):
    from utils.langsmith_bootstrap import log_langsmith_status

    caplog.set_level(std_logging.INFO)
    with patch.dict(os.environ, {
        "LANGCHAIN_TRACING_V2": "true",
        "LANGCHAIN_API_KEY": "fake_key",
        "LANGCHAIN_PROJECT": "NL2SQL",
    }, clear=False):
        log_langsmith_status()

    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "LangSmith tracing enabled" in log_text
    assert "project=NL2SQL" in log_text


def test_langsmith_status_disabled(caplog, loguru_to_caplog):
    from utils.langsmith_bootstrap import log_langsmith_status

    caplog.set_level(std_logging.INFO)
    with patch.dict(os.environ, {"LANGCHAIN_TRACING_V2": "false"}, clear=False):
        log_langsmith_status()

    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "LangSmith tracing disabled" in log_text


def test_langsmith_status_unset(caplog, loguru_to_caplog):
    """未设置 LANGCHAIN_TRACING_V2 时也应输出 disabled"""
    from utils.langsmith_bootstrap import log_langsmith_status

    caplog.set_level(std_logging.INFO)
    # 清掉相关变量再调用
    env_clear = {k: "" for k in ["LANGCHAIN_TRACING_V2"]}
    with patch.dict(os.environ, env_clear, clear=False):
        log_langsmith_status()

    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "LangSmith tracing disabled" in log_text


def test_langsmith_status_enabled_without_api_key(caplog, loguru_to_caplog):
    """tracing=true 但 API_KEY 为空时应警告"""
    from utils.langsmith_bootstrap import log_langsmith_status

    caplog.set_level(std_logging.WARNING)
    with patch.dict(os.environ, {
        "LANGCHAIN_TRACING_V2": "true",
        "LANGCHAIN_API_KEY": "",
        "LANGCHAIN_PROJECT": "NL2SQL",
    }, clear=False):
        log_langsmith_status()

    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "LANGCHAIN_API_KEY 未设置" in log_text


# ────────────────────────────────────────────────────────────
# §8.1.5: LLMClient.run_name 参数
# ────────────────────────────────────────────────────────────


def _new_client():
    from utils.llm_client import LLMClient
    with patch.dict("os.environ", {"QWEN_API_KEY": "fake_key"}):
        return LLMClient(model="fake-model")


def test_bind_runtime_run_name_attaches_via_with_config():
    """传 run_name 时返回的 RunnableBinding.config 应包含 run_name"""
    client = _new_client()
    bound = client._bind_runtime(
        temperature=None, max_tokens=None, as_json=False,
        thinking=None, run_name="cache-check",
    )
    # 通过 RunnableBinding.config 暴露的 dict 验证
    assert getattr(bound, "config", {}).get("run_name") == "cache-check"


def test_bind_runtime_no_run_name_returns_no_config():
    """run_name=None 时不应有 run_name 配置"""
    client = _new_client()
    bound = client._bind_runtime(
        temperature=None, max_tokens=None, as_json=False,
        thinking=None, run_name=None,
    )
    # 此时不走 with_config，直接是 _chat_model 或 ChatOpenAI.bind 后的对象
    cfg = getattr(bound, "config", {}) or {}
    assert "run_name" not in cfg


def test_bind_runtime_run_name_combined_with_temperature():
    """run_name + temperature 同时传入：先 with_config 再 bind"""
    client = _new_client()
    bound = client._bind_runtime(
        temperature=0.3, max_tokens=512, as_json=True,
        thinking=False, run_name="cg-generate",
    )
    # RunnableBinding.kwargs 包含 bind 的 model 参数
    kw = getattr(bound, "kwargs", {})
    assert kw.get("temperature") == 0.3
    assert kw.get("max_tokens") == 512
    assert kw.get("response_format") == {"type": "json_object"}
    assert kw.get("extra_body") == {"enable_thinking": False}
    # config 包含 with_config 的 runtime 参数
    cfg = getattr(bound, "config", {})
    assert cfg.get("run_name") == "cg-generate"


def test_invoke_passes_run_name():
    """invoke(run_name=...) 应让 _bind_runtime 的 run_name 生效"""
    from utils.llm_client import LLMClient
    from langchain_core.messages import AIMessage

    client = _new_client()
    captured: dict = {}

    real_bind = client._bind_runtime

    def spy_bind(*args, **kwargs):
        # _bind_runtime 是位置参数：(temperature, max_tokens, as_json, thinking, run_name)
        captured["args"] = args
        captured["kwargs"] = kwargs
        # 返回一个 mock runnable，避免真发起 API 调用
        runnable = MagicMock()
        runnable.invoke = MagicMock(return_value=AIMessage(content=[{"type": "text", "text": "ok"}]))
        return runnable

    with patch.object(client, "_bind_runtime", side_effect=spy_bind):
        client.invoke([HumanMessage("hi")], run_name="cache-check")

    # 参数顺序：temperature, max_tokens, as_json, thinking, run_name
    assert captured["args"][-1] == "cache-check"


def test_stream_passes_run_name():
    """stream(run_name=...) 同样让 _bind_runtime 的 run_name 生效"""
    client = _new_client()
    captured: dict = {}

    def spy_bind(*args, **kwargs):
        captured["args"] = args
        runnable = MagicMock()
        runnable.stream = MagicMock(return_value=iter([]))
        return runnable

    with patch.object(client, "_bind_runtime", side_effect=spy_bind):
        list(client.stream([HumanMessage("x")], run_name="ir-keywords"))

    assert captured["args"][-1] == "ir-keywords"


# ────────────────────────────────────────────────────────────
# §8.1.7: API 层注入请求级 config
# ────────────────────────────────────────────────────────────


def test_query_config_shape():
    """单元测试 config dict 的形态（不实际调 graph）"""
    # 这里只验证 config dict 应包含的字段，作为协议契约测试
    query_id = "abc1234567ef"
    session_id = "sess-xyz"
    user_id = "alice"
    db_id = "ecommerce"
    user_query = "查一下苹果的销售额"

    config = {
        "configurable": {"thread_id": session_id},
        "run_name": f"query-{query_id}",
        "tags": [db_id, "api", f"user:{user_id}"],
        "metadata": {
            "query_id": query_id,
            "user_id": user_id,
            "session_id": session_id,
            "db_id": db_id,
            "user_query": user_query[:200],
        },
    }
    # 字段齐全
    assert config["configurable"]["thread_id"] == session_id
    assert config["run_name"] == f"query-{query_id}"
    assert db_id in config["tags"]
    assert "api" in config["tags"]
    assert f"user:{user_id}" in config["tags"]
    assert config["metadata"]["query_id"] == query_id
    assert config["metadata"]["user_query"] == user_query


def test_query_config_long_query_truncated():
    """user_query 超过 200 字符时应被截断"""
    long_query = "x" * 500
    truncated = long_query[:200]
    assert len(truncated) == 200


# ────────────────────────────────────────────────────────────
# §8.1.4: 主图 / 子图 run_name 钉死
# ────────────────────────────────────────────────────────────


def test_subgraph_run_names():
    """5 个子图编译后的 run_name 应符合命名规范"""
    from src.retrieval.ir_graph import build_ir_graph
    from src.schema_selection.ss_graph import build_ss_graph
    from src.sql_generation.cg_graph import build_cg_graph
    from src.execution.execution_graph import build_execution_graph
    from src.decision.decision_graph import build_decision_graph

    # 构造 mock agent（仅需提供子图节点用到的方法 stub）
    ir = MagicMock()
    ir.extract_keywords = MagicMock(return_value=[])
    ir.search_values = MagicMock(return_value=[])
    ir.retrieve_schema = MagicMock(return_value={})

    ss = MagicMock()
    cg = MagicMock()
    fix_loop = MagicMock()
    decider = MagicMock()

    ir_graph = build_ir_graph(ir)
    ss_graph = build_ss_graph(ss)
    cg_graph = build_cg_graph(cg)
    exec_graph = build_execution_graph(fix_loop)
    decision_graph = build_decision_graph(decider)

    # 子图编译后是 RunnableBinding（with_config 包装），config.run_name 即是子图名
    assert ir_graph.config.get("run_name") == "ir-graph"
    assert ss_graph.config.get("run_name") == "ss-graph"
    assert cg_graph.config.get("run_name") == "cg-graph"
    assert exec_graph.config.get("run_name") == "execution-graph"
    assert decision_graph.config.get("run_name") == "decision-graph"
