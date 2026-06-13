# ============================================================================
# NL2SQL Agent 主入口
# ============================================================================
#
# 使用方法:
#   python -m src.main
#
# 或者在代码中调用:
#   from src.main import NL2SQLAgent
#   agent = NL2SQLAgent()
#   result = agent.query("显示去年的销售额")
#
# ============================================================================


import os
import sys
from typing import Optional, Dict, Any

# 添加根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from utils.langsmith_bootstrap import log_langsmith_status


class NL2SQLAgent:
    """
    NL2SQL Agent 主类 - 整合所有模块

    架构:
    ┌─────────────┐
    │ NL2SQLAgent │
    └──────┬──────┘
           │
     ┌─────┴────────────────────────────────┐
     │                                      │
    ┌▼─────┐  ┌─────────┐  ┌────────────┐  ┌───────┐
    │Preproc│─▶│ Retrieval│▶│ SchemaSelect│▶│ SQLGen│
    │预处理  │  │ 检索     │  │ Schema 选择  │  │生成器  │
    └───────┘  └─────────┘  └────────────┘  └───┬───┘
                                                  │
    ┌───────────┐  ┌─────────┐  ┌─────────────┐  │
    │ Terminal  │◀─│ Decision│◀─│   Executor  │◀─┘
    │ 终端界面   │  │ 决策     │  │ 执行引擎    │
    └───────────┘  └─────────┘  └─────────────┘

    Attributes:
        config: 配置字典
        preprocessing: 预处理模块
        retrieval: 检索模块
        schema_selector: Schema 选择器
        sql_generator: SQL 生成器
        executor: 执行引擎
        decision: 决策模块
        monitor: 监控器
    """

    def __init__(self, config: Dict[str, Any] = None):
        """
        初始化 NL2SQL Agent

        Args:
            config: 配置字典（可选）
                   {
                       "db_path": "data/bird_sql.db",
                       "vector_store_path": "./vector_store",
                       "llm_model": "qwen3-max-2025-09-23",
                       "embedding_model": "BAAI/bge-m3",
                       "num_candidates": 5,
                       ...
                   }

        TODO: 您需要实现的细节
        1. 加载配置（从文件或使用默认值）
        2. 初始化各个模块组件
        3. 加载 LSH 索引和向量存储
        """
        self.config = config or {}
        self.preprocessing = None
        self.retrieval = None
        self.schema_selector = None
        self.sql_generator = None
        self.executor = None
        self.decision = None
        self.monitor = None

        # TODO: 初始化各模块
        # self._init_components()

    def _init_components(self):
        """
        初始化所有组件

        TODO: 实现此方法
        - 初始化 DatabaseConnector
        - 初始化 LSHIndexer 并加载索引
        - 初始化 SchemaVectorizer
        - 初始化 VectorStoreManager
        - 初始化 InformationRetrieval
        - 初始化 SchemaSelector
        - 初始化 SQLGenerator
        - 初始化 SQLExecutor
        - 初始化 SelfConsistencyDecision
        - 初始化 LangSmithMonitor
        """
        pass

    def query(self, user_query: str) -> dict:
        """
        处理用户查询（完整流程）

        Args:
            user_query: 用户的自然语言查询
                       例如："显示去年北京地区的销售额"

        Returns:
            dict: 查询结果
                 {
                     "success": True/False,
                     "sql": "生成的 SQL 语句",
                     "result": [...],  # 执行结果
                     "execution_time": 1.23,  # 总耗时（秒）
                     "candidates": [...],  # 所有候选 SQL
                     "error": None  # 如果有错误
                 }

        TODO: 完整的处理流程
        1. 开始监控计时
        2. 信息检索 (IR)
        3. Schema 选择 (SS)
        4. SQL 生成 (CG)
        5. 执行和验证
        6. Self-Consistency 决策
        7. 返回结果
        """
        pass

    def run_interactive(self):
        """
        运行交互式界面

        TODO:
        - 初始化 TerminalInterface
        - 启动 interactive_loop
        """
        pass


def main():
    """主函数 - 命令行入口"""
    load_dotenv()
    log_langsmith_status()  # §8.1.1：启动时确认 LangSmith tracing 状态
    print("NL2SQL Agent 系统启动中...")

    # 创建 Agent
    agent = NL2SQLAgent()

    # 运行交互式界面
    agent.run_interactive()


if __name__ == "__main__":
    main()