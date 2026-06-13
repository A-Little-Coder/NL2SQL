"""LangSmith 接入 — 路径 A（环境变量驱动）的启动确认日志

设计（§7 / §7a 决策）：
  - 不实现自定义 LangSmithMonitor 包装类
  - LangChain 1.x + LangGraph 在 LANGCHAIN_TRACING_V2=true 时自动上报
  - 本模块仅负责启动时打印一行确认日志，让运维/开发者一眼看到 tracing 状态

调用方：
  - src/api/app.py 的 lifespan startup
  - src/main.py 的 main() / NL2SQLAgent.__init__
"""

import os

from loguru import logger


def log_langsmith_status() -> None:
    """读取 LANGCHAIN_* 环境变量并打印 LangSmith tracing 状态

    输出示例：
      启用：``LangSmith tracing enabled: project=NL2SQL``
      关闭：``LangSmith tracing disabled``

    判定规则（与 LangChain 官方一致）：
      LANGCHAIN_TRACING_V2 严格 == "true"（小写）→ 启用
      其余值（空 / "false" / "0" / 未设置）→ 关闭
    """
    enabled = os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"
    if not enabled:
        logger.info("LangSmith tracing disabled")
        return

    project = os.getenv("LANGCHAIN_PROJECT", "default")
    api_key = os.getenv("LANGCHAIN_API_KEY", "")
    if not api_key:
        logger.warning(
            f"LangSmith tracing enabled: project={project} (但 LANGCHAIN_API_KEY 未设置，"
            f"实际不会上报；请配置 .env)"
        )
        return

    logger.info(f"LangSmith tracing enabled: project={project}")
