# ============================================================================
# NL2SQL 端到端 Mock 测试
# ============================================================================
# 不依赖 LLM API 和 BGE-M3，验证主链路流程编排正确
#
# 运行方法:
#   python -m tests.test_e2e_mock
# ============================================================================


import os
import sys
import sqlite3
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.preprocessing.database_connector import DatabaseConnector
from src.retrieval.information_retrieval import (
    InformationRetrieval, RetrievedContext, RetrievedItem,
)
from src.schema_selection.schema_selector import (
    SchemaSelector, MSchemaTable, MSchemaColumn,
)
from src.sql_generation.sql_generator import SQLGenerator, SQLCandidate, SQLStatus
from src.execution.executor import SQLExecutor, SQLFixLoop
from src.decision.self_consistency import SelfConsistencyDecision


def create_test_db(db_path: str):
    """创建测试用 SQLite 数据库"""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY,
            name TEXT,
            category TEXT,
            price REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            product_id INTEGER,
            quantity INTEGER,
            order_date TEXT,
            region TEXT,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    """)
    # 插入测试数据
    products = [
        (1, '苹果', '水果', 5.5),
        (2, '香蕉', '水果', 3.0),
        (3, '华为手机', '电子产品', 3999.0),
        (4, '苹果手机', '电子产品', 6999.0),
        (5, '橙子', '水果', 4.0),
    ]
    orders = [
        (1, 1, 100, '2024-01-15', '北京'),
        (2, 2, 200, '2024-01-16', '上海'),
        (3, 3, 50,  '2024-02-10', '北京'),
        (4, 4, 30,  '2024-02-11', '广州'),
        (5, 5, 150, '2024-03-01', '北京'),
        (6, 1, 80,  '2024-03-05', '上海'),
        (7, 4, 20,  '2024-03-10', '北京'),
    ]
    conn.executemany("INSERT OR REPLACE INTO products VALUES (?, ?, ?, ?)", products)
    conn.executemany("INSERT OR REPLACE INTO orders VALUES (?, ?, ?, ?, ?)", orders)
    conn.commit()
    conn.close()


class TestE2EMock(unittest.TestCase):
    """端到端 Mock 测试 - 不调真实 API"""

    tmp_dir = None
    db_path = None
    connector = None

    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.mkdtemp(prefix="nl2sql_e2e_")
        cls.db_path = os.path.join(cls.tmp_dir, "test.db")
        create_test_db(cls.db_path)
        cls.connector = DatabaseConnector(cls.db_path, db_type="sqlite")

    @classmethod
    def tearDownClass(cls):
        cls.connector.disconnect()
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def _mock_ir_result(self, query: str) -> RetrievedContext:
        """模拟 IR 返回结果"""
        return RetrievedContext(
            tables=[
                RetrievedItem(item_type="table", name="products", score=0.9),
                RetrievedItem(item_type="table", name="orders", score=0.85),
            ],
            columns=[
                RetrievedItem(item_type="column", name="name", table_name="products",
                              score=0.9, metadata={"data_type": "TEXT"}),
                RetrievedItem(item_type="column", name="category", table_name="products",
                              score=0.8, metadata={"data_type": "TEXT"}),
                RetrievedItem(item_type="column", name="price", table_name="products",
                              score=0.7, metadata={"data_type": "REAL"}),
                RetrievedItem(item_type="column", name="product_id", table_name="orders",
                              score=0.85, metadata={"data_type": "INTEGER", "is_foreign_key": True}),
                RetrievedItem(item_type="column", name="quantity", table_name="orders",
                              score=0.8, metadata={"data_type": "INTEGER"}),
                RetrievedItem(item_type="column", name="region", table_name="orders",
                              score=0.9, metadata={"data_type": "TEXT"}),
                RetrievedItem(item_type="column", name="order_date", table_name="orders",
                              score=0.6, metadata={"data_type": "TEXT"}),
            ],
            values=[
                RetrievedItem(item_type="value", name="苹果", table_name="products",
                              score=0.85, metadata={"column_name": "name"}),
                RetrievedItem(item_type="value", name="北京", table_name="orders",
                              score=0.9, metadata={"column_name": "region"}),
            ],
            keywords=["苹果", "北京", "销售额"],
            lsh_hit_count=2,
            vector_top_scores=[0.9, 0.85, 0.8],
        )

    def test_full_pipeline_beijing_apple(self):
        """测试完整流程：查一下北京地区苹果的销售额"""
        print("\n" + "=" * 60)
        print("端到端 Mock 测试：查一下北京地区苹果的销售额")
        print("=" * 60)

        # Step 1: IR（模拟）
        print("\n[Step 1] 信息检索 (IR)")
        context = self._mock_ir_result("查一下北京地区苹果的销售额")
        print(f"  关键词: {context.keywords}")
        print(f"  表: {[t.name for t in context.tables]}")
        print(f"  值: {[(v.name, v.table_name) for v in context.values]}")
        print(f"  LSH 命中: {context.lsh_hit_count}")

        # Step 2: SS
        print("\n[Step 2] Schema 选择 (SS)")
        selector = SchemaSelector(db_connector=self.connector)
        mschema = selector.to_mschema(context)
        # 手动设分数（Mock LLM）
        for tbl in mschema:
            for col in tbl.columns:
                if col.name in ("name", "region", "quantity", "product_id"):
                    col.relevance_score = 0.9
                elif col.name in ("category", "price", "order_date"):
                    col.relevance_score = 0.3
                else:
                    col.relevance_score = 0.5

        filtered = selector.filter_columns(mschema)
        for tbl in filtered:
            cols = [c.name for c in tbl.columns]
            print(f"  {tbl.name}: {cols}")

        # Step 3: CG（模拟 LLM 返回 SQL）
        print("\n[Step 3] SQL 生成 (CG)")
        mock_llm = MagicMock()
        mock_llm.stream.return_value = iter([(__import__("json").dumps({
            "candidates": [
                {
                    "sql": "SELECT p.name, SUM(o.quantity * p.price) AS total_sales FROM products p JOIN orders o ON p.id = o.product_id WHERE p.name = '苹果' AND o.region = '北京'",
                    "reason": "JOIN 查询苹果在北京的销售额",
                },
                {
                    "sql": "SELECT SUM(o.quantity * p.price) AS total FROM products p JOIN orders o ON p.id = o.product_id WHERE p.name LIKE '%苹果%' AND o.region = '北京'",
                    "reason": "使用 LIKE 模糊匹配",
                },
            ]
        }, ensure_ascii=False), None)])
        generator = SQLGenerator(llm_client=mock_llm)
        candidates = generator.generate(filtered, "查一下北京地区苹果的销售额")
        for c in candidates:
            print(f"  SQL: {c.sql}")
            print(f"  状态: {c.status.value}")

        # Step 4: Execution
        print("\n[Step 4] SQL 执行")
        executor = SQLExecutor(db_connector=self.connector)
        for cand in candidates:
            result = executor.execute(cand.sql)
            cand.result = result.result_data
            cand.execution_time = result.execution_time
            cand.status = SQLStatus.SUCCESS if result.success else SQLStatus.FAILED
            if result.success:
                print(f"  [{cand.id}] 成功, {len(result.result_data)} 行, {result.execution_time:.4f}s")
                print(f"    结果: {result.result_data}")
            else:
                print(f"  [{cand.id}] 失败: {result.error.original_message}")

        # Step 5: Decision
        print("\n[Step 5] Self-Consistency 决策")
        decider = SelfConsistencyDecision()
        decision = decider.decide(candidates, "查一下北京地区苹果的销售额")
        print(f"  最终 SQL: {decision.selected_sql}")
        print(f"  结果: {decision.selected_result}")
        print(f"  耗时: {decision.execution_time:.4f}s")
        print(f"  决策理由: {decision.decision_reason}")

        # 验证
        self.assertIsNotNone(decision.selected_sql)
        self.assertIsNotNone(decision.selected_result)
        self.assertIn("苹果", decision.selected_sql)

    def test_full_pipeline_simple_query(self):
        """测试简单查询：有多少种水果"""
        print("\n" + "=" * 60)
        print("端到端 Mock 测试：有多少种水果")
        print("=" * 60)

        context = RetrievedContext(
            tables=[RetrievedItem(item_type="table", name="products", score=0.9)],
            columns=[
                RetrievedItem(item_type="column", name="category", table_name="products",
                              score=0.9, metadata={"data_type": "TEXT"}),
                RetrievedItem(item_type="column", name="name", table_name="products",
                              score=0.7, metadata={"data_type": "TEXT"}),
            ],
            values=[RetrievedItem(item_type="value", name="水果", table_name="products",
                                  score=0.9, metadata={"column_name": "category"})],
            keywords=["水果", "种类"],
            lsh_hit_count=1,
        )

        # SS
        selector = SchemaSelector(db_connector=self.connector)
        mschema = selector.to_mschema(context)
        for tbl in mschema:
            for col in tbl.columns:
                col.relevance_score = 0.9

        # CG
        mock_llm = MagicMock()
        mock_llm.stream.return_value = iter([(__import__("json").dumps({
            "candidates": [
                {"sql": "SELECT COUNT(*) AS fruit_count FROM products WHERE category = '水果'",
                 "reason": "统计水果种类"},
            ]
        }, ensure_ascii=False), None)])
        generator = SQLGenerator(llm_client=mock_llm)
        candidates = generator.generate(mschema, "有多少种水果")

        # Execute
        executor = SQLExecutor(db_connector=self.connector)
        for cand in candidates:
            result = executor.execute(cand.sql)
            cand.result = result.result_data
            cand.execution_time = result.execution_time
            cand.status = SQLStatus.SUCCESS if result.success else SQLStatus.FAILED

        # Decision
        decider = SelfConsistencyDecision()
        decision = decider.decide(candidates, "有多少种水果")

        print(f"\n  SQL: {decision.selected_sql}")
        print(f"  结果: {decision.selected_result}")
        print(f"  理由: {decision.decision_reason}")

        self.assertIsNotNone(decision.selected_result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
