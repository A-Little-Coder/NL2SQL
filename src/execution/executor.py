# ============================================================================
# 执行引擎 - SQL 执行和错误处理
# ============================================================================
# 功能说明:
#   1. 安全执行 SQL 查询（支持 EXPLAIN 预验证）
#   2. 捕获和结构化错误信息
#   3. 支持错误修正循环（最多 2 次重试）
# ============================================================================


import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Dict, List
from enum import Enum
from loguru import logger


class ErrorType(Enum):
    """错误类型枚举"""
    SYNTAX_ERROR = "syntax_error"
    SEMANTIC_ERROR = "semantic_error"
    RUNTIME_ERROR = "runtime_error"
    TIMEOUT_ERROR = "timeout_error"
    PERMISSION_ERROR = "permission_error"
    UNKNOWN = "unknown"


@dataclass
class StructuredError:
    """结构化的错误信息"""
    error_type: ErrorType
    original_message: str
    table_name: str = None
    column_name: str = None
    suggested_fix: str = None

    def to_prompt_format(self) -> str:
        msg = f"错误类型：{self.error_type.value}\n"
        msg += f"错误信息：{self.original_message}"
        if self.table_name:
            msg += f"\n涉及的表：{self.table_name}"
        if self.column_name:
            msg += f"\n涉及的列：{self.column_name}"
        if self.suggested_fix:
            msg += f"\n建议修复：{self.suggested_fix}"
        return msg


@dataclass
class ExecutionResult:
    """SQL 执行结果"""
    success: bool
    sql: str
    result_data: Any = None
    execution_time: float = None
    row_count: int = None
    error: StructuredError = None
    explain_plan: str = None


class ErrorHandler:
    """
    错误处理器 - 错误分类和修正建议
    """

    @staticmethod
    def classify_error(error_msg: str) -> ErrorType:
        """
        分类错误类型

        Args:
            error_msg: 错误消息

        Returns:
            ErrorType: 错误类型
        """
        if not error_msg:
            return ErrorType.UNKNOWN

        error_lower = error_msg.lower()

        if 'syntax' in error_lower or 'near "' in error_lower or 'near \'' in error_lower:
            return ErrorType.SYNTAX_ERROR
        elif 'no such table' in error_lower:
            return ErrorType.SEMANTIC_ERROR
        elif 'no such column' in error_lower or 'unknown column' in error_lower:
            return ErrorType.SEMANTIC_ERROR
        elif 'timeout' in error_lower or 'timed out' in error_lower:
            return ErrorType.TIMEOUT_ERROR
        elif 'permission' in error_lower or 'access denied' in error_lower:
            return ErrorType.PERMISSION_ERROR
        else:
            return ErrorType.RUNTIME_ERROR

    @staticmethod
    def extract_table_column(error_msg: str) -> tuple:
        """
        从错误消息中提取涉及的表和列

        Args:
            error_msg: 错误消息

        Returns:
            tuple: (table_name, column_name)
        """
        if not error_msg:
            return None, None

        table_name = None
        column_name = None

        # SQLite 风格: "no such table: xxx"
        m = re.search(r'no such table:\s*(\S+)', error_msg, re.IGNORECASE)
        if m:
            table_name = m.group(1).strip('"\'`')

        # SQLite 风格: "no such column: xxx.yyy" or "no such column: yyy"
        m = re.search(r'no such column:\s*(\S+)', error_msg, re.IGNORECASE)
        if m:
            col_full = m.group(1).strip('"\'`')
            if '.' in col_full:
                parts = col_full.split('.', 1)
                if not table_name:
                    table_name = parts[0]
                column_name = parts[1]
            else:
                column_name = col_full

        return table_name, column_name

    @staticmethod
    def suggest_fix(error: StructuredError) -> str:
        """
        根据错误类型提供修复建议

        Args:
            error: 结构化错误

        Returns:
            str: 修复建议
        """
        fixes = {
            ErrorType.SYNTAX_ERROR: "请检查 SQL 语法，确保关键字拼写正确，括号匹配。",
            ErrorType.SEMANTIC_ERROR: "请检查表名或列名是否存在，注意大小写和反引号使用。",
            ErrorType.TIMEOUT_ERROR: "查询超时，请简化查询或增加超时时间。",
            ErrorType.PERMISSION_ERROR: "没有执行此查询的权限，请检查用户权限。",
            ErrorType.RUNTIME_ERROR: "运行时错误，请检查数据类型和查询条件。",
        }
        return fixes.get(error.error_type, "请检查 SQL 语句。")


class SQLExecutor:
    """
    SQL 执行器 - 安全执行和错误处理

    Attributes:
        db_connector: 数据库连接器
        default_timeout: 默认超时时间（秒）
    """

    def __init__(self, db_connector=None, default_timeout: int = 30):
        self.db_connector = db_connector
        self.default_timeout = default_timeout

    def explain(self, sql: str) -> ExecutionResult:
        """
        执行 EXPLAIN 验证 SQL

        Args:
            sql: SQL 语句

        Returns:
            ExecutionResult: 包含执行计划的结果
        """
        if not self.db_connector:
            return ExecutionResult(
                success=False, sql=sql,
                error=self._build_error("数据库连接器未设置", sql),
            )

        try:
            success, plan, error_msg = self.db_connector.explain_query(sql)
            if success:
                return ExecutionResult(
                    success=True, sql=sql, explain_plan=plan,
                )
            else:
                return ExecutionResult(
                    success=False, sql=sql,
                    error=self._build_error(error_msg or "EXPLAIN 失败", sql),
                )
        except Exception as e:
            return ExecutionResult(
                success=False, sql=sql,
                error=self._build_error(str(e), sql),
            )

    def execute(self, sql: str, timeout: int = None) -> ExecutionResult:
        """
        执行 SQL 查询

        Args:
            sql: SQL 语句
            timeout: 超时时间（秒）

        Returns:
            ExecutionResult: 执行结果
        """
        if not self.db_connector:
            return ExecutionResult(
                success=False, sql=sql,
                error=self._build_error("数据库连接器未设置", sql),
            )

        actual_timeout = timeout or self.default_timeout
        start_time = time.time()

        try:
            success, result, error_msg = self.db_connector.execute_query(
                sql, timeout=actual_timeout,
            )
            elapsed = time.time() - start_time

            if success:
                row_count = len(result) if isinstance(result, list) else result
                return ExecutionResult(
                    success=True, sql=sql,
                    result_data=result,
                    execution_time=elapsed,
                    row_count=row_count if isinstance(row_count, int) else None,
                )
            else:
                return ExecutionResult(
                    success=False, sql=sql,
                    execution_time=elapsed,
                    error=self._build_error(error_msg or "执行失败", sql),
                )
        except Exception as e:
            elapsed = time.time() - start_time
            return ExecutionResult(
                success=False, sql=sql,
                execution_time=elapsed,
                error=self._build_error(str(e), sql),
            )

    def _build_error(self, error_msg: str, sql: str) -> StructuredError:
        """构建结构化错误"""
        return self._parse_error(error_msg)

    def _parse_error(self, error_msg: str) -> StructuredError:
        """
        解析原始错误消息，提取结构化信息

        Args:
            error_msg: 原始错误消息

        Returns:
            StructuredError: 结构化的错误信息
        """
        err_type = ErrorHandler.classify_error(error_msg)
        table_name, column_name = ErrorHandler.extract_table_column(error_msg)

        error = StructuredError(
            error_type=err_type,
            original_message=error_msg,
            table_name=table_name,
            column_name=column_name,
        )
        error.suggested_fix = ErrorHandler.suggest_fix(error)
        return error


# ============================================================================
# 错误修正循环
# ============================================================================

SQL_FIX_PROMPT = """你是 SQL 专家。下面的 SQL 执行失败了，请修正它。

原始用户查询: "{user_query}"

失败的 SQL:
{sql}

错误信息:
{error_info}

可用 Schema:
{schema_text}

请生成修正后的 SQL（只生成 SELECT 查询，禁止修改数据），返回 JSON：
{{"sql": "修正后的SQL", "reason": "修正理由"}}
"""


class SQLFixLoop:
    """
    SQL 错误修正循环 - 最多 N 次重试

    Attributes:
        executor: SQLExecutor 实例
        llm_client: LLM 客户端
        max_retries: 最大重试次数
    """

    def __init__(self, executor: SQLExecutor, llm_client=None, max_retries: int = 2):
        self.executor = executor
        self.llm_client = llm_client
        self.max_retries = max_retries

    def run(self, sql: str, user_query: str, schema_text: str = "") -> ExecutionResult:
        """
        执行 SQL 并尝试修正错误

        Args:
            sql: 初始 SQL
            user_query: 原始用户查询
            schema_text: schema 描述文本

        Returns:
            ExecutionResult: 最终执行结果
        """
        current_sql = sql
        last_result = None

        for attempt in range(self.max_retries + 1):
            result = self.executor.execute(current_sql)

            if result.success:
                if attempt > 0:
                    logger.info(f"SQL 修正成功（第 {attempt} 次重试）")
                return result

            last_result = result
            logger.warning(
                f"SQL 执行失败（attempt {attempt + 1}/{self.max_retries + 1}）: "
                f"{result.error.original_message[:100] if result.error else 'unknown'}"
            )

            # 如果还有重试机会且有 LLM，尝试修正
            if attempt < self.max_retries and self.llm_client:
                fixed_sql = self._try_fix(current_sql, result.error, user_query, schema_text)
                if fixed_sql and fixed_sql != current_sql:
                    current_sql = fixed_sql
                    logger.info(f"LLM 提供修正建议，重试中: {fixed_sql[:80]}")
                    continue
            break

        return last_result

    def _try_fix(self, sql: str, error: StructuredError, user_query: str,
                 schema_text: str) -> Optional[str]:
        """
        尝试用 LLM 修正 SQL

        Args:
            sql: 失败的 SQL
            error: 错误信息
            user_query: 原始查询
            schema_text: schema 描述

        Returns:
            Optional[str]: 修正后的 SQL，无法修正时返回 None
        """
        if not self.llm_client or not error:
            return None

        try:
            prompt = SQL_FIX_PROMPT.format(
                user_query=user_query,
                sql=sql,
                error_info=error.to_prompt_format(),
                schema_text=schema_text or "(未提供 schema)",
            )
            messages = [
                {"role": "system", "content": "你是 SQL 修正专家，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ]
            result = self.llm_client.chat_json(messages, temperature=0.0)
            fixed = result.get("sql", "").strip()
            return fixed if fixed else None
        except Exception as e:
            logger.error(f"LLM 修正失败: {e}")
            return None

    # ------------------------------------------------------------------
    # LangGraph 子图接口（§18.6 / §18.8）
    # ------------------------------------------------------------------
    def build_graph(self):
        """
        返回 Execution Agent 的已编译 LangGraph 子图

        子图节点：execute → (条件) llm_fix → execute，循环最多 max_retries 次
        子图输入字段：sql, user_query, schema_text
        子图输出字段：result (ExecutionResult), fix_history (List[str])
        """
        from src.execution.execution_graph import build_execution_graph
        return build_execution_graph(self)
