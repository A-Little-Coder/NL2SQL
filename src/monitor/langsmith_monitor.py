# ============================================================================
# 监控和用户界面模块
# ============================================================================
# 功能说明:
#   1. LangSmith 全流程监控集成
#   2. Terminal 交互式界面，支持流式输出
#   3. 思考过程可视化
#
# 待您补充的细节:
#   1. LangSmith trace 的具体配置
#   2. 流式输出的实现（使用 langchain 或手动）
# ============================================================================


import os
from typing import Optional, Callable
from datetime import datetime


class LangSmithMonitor:
    """
    LangSmith 监控器 - 全流程追踪

    功能:
    - 记录每个阶段的开始/结束时间
    - 捕获输入输出
    - 错误追踪
    - 性能指标收集

    用法:
    ```python
    monitor = LangSmithMonitor(project_name="nl2sql")

    with monitor.trace("keyword_extraction"):
        keywords = extract_keywords(query)
    ```

    Attributes:
        project_name: LangSmith 项目名称
        enabled: 是否启用监控
    """

    def __init__(self, project_name: str = "nl2sql", enabled: bool = True):
        """
        初始化监控器

        Args:
            project_name: LangSmith 项目名称
            enabled: 是否启用监控（设置 False 可跳过无 API key 的情况）
        """
        self.project_name = project_name
        self.enabled = enabled
        self._current_trace = None
        # TODO: 初始化 LangSmith client
        # from langsmith import Client
        # self.client = Client() if enabled else None

    def trace(self, span_name: str, inputs: dict = None):
        """
        创建一个 trace 上下文

        Args:
            span_name: 跟踪名称（如 "preprocessing", "retrieval"）
            inputs: 输入数据（可选）

        Returns:
            context manager: 用于 with 语句

        TODO:
        - 使用 langsmith.trace 或手动记录
        - 支持嵌套 trace
        """
        pass

    def record_event(self, event_name: str, data: dict = None):
        """
        记录一个事件

        Args:
            event_name: 事件名称
            data: 事件数据
        """
        pass

    def record_error(self, error: Exception, context: dict = None):
        """
        记录错误

        Args:
            error: 异常对象
            context: 错误发生的上下文
        """
        pass


class TerminalInterface:
    """
    Terminal 交互式界面

    功能:
    - 接收用户输入
    - 流式显示执行过程和思考过程
    - 显示最终结果

    视觉效果设计:
    ```
    ════════════════════════════════════════════
       NL2SQL Agent System
    ════════════════════════════════════════════

    用户：显示去年北京地区的销售额

    ┌─ 处理开始 ───────────────────────────────┐
    [✓] 预处理阶段 (0.5s)
        - 数据库连接：bird_sql_001.db
        - 加载 LSH 索引...

    [→] 信息检索中...
    [→] Schema 选择中...
    [→] SQL 生成中...
    [→] 执行验证中...
    └───────────────────────────────────────────┘

    生成的 SQL:
    SELECT amount FROM sales
    WHERE region = '北京' AND year = 2023

    执行结果:
    +----------+
    |  amount  |
    +----------+
    | 1500000  |
    | 2300000  |
    +----------+

    总计耗时：2.3 秒
    ```

    Attributes:
        monitor: LangSmithMonitor 实例
        stream_callback: 流式输出回调函数
    """

    def __init__(self, monitor: LangSmithMonitor = None):
        """
        初始化终端界面

        Args:
            monitor: LangSmithMonitor 实例
        """
        self.monitor = monitor or LangSmithMonitor(enabled=False)
        self.stream_callback = None

    def set_stream_callback(self, callback: Callable):
        """
        设置流式输出回调

        Args:
            callback: 回调函数 func(event_type: str, message: str)
        """
        self.stream_callback = callback

    def display_header(self):
        """显示系统标题"""
        pass

    def display_stage(self, stage_name: str, status: str = "→"):
        """
        显示当前阶段

        Args:
            stage_name: 阶段名称
            status: 状态符号 ("✓" | "→" | "✗")
        """
        pass

    def display_sql(self, sql: str):
        """显示生成的 SQL"""
        pass

    def display_result(self, result: any):
        """显示执行结果"""
        pass

    def display_error(self, error: str):
        """显示错误信息"""
        pass

    def interactive_loop(self):
        """
        交互式循环主函数

        TODO:
        - 显示欢迎信息
        - 循环读取用户输入
        - 调用 NL2SQL 处理
        - 流式显示结果
        """
        pass