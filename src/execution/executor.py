# ============================================================================
# 执行引擎 - SQL 执行和错误处理
# ============================================================================
# 功能说明:
#   1. 安全执行 SQL 查询（支持 EXPLAIN 预验证）
#   2. 捕获和结构化错误信息
#   3. 支持错误修正循环（最多 2 次重试）
#
# 输入:
#   - sql: SQL 语句
#   - database_connector: 数据库连接实例
#
# 输出:
#   - ExecutionResult: 执行结果对象
#
# 待您补充的细节:
#   1. EXPLAIN 执行的解析
#   2. 错误信息的分类和提取
# ============================================================================


from dataclasses import dataclass, field
from typing import Any, Optional, Dict, List
from enum import Enum
import time


class ErrorType(Enum):
    """错误类型枚举"""
    SYNTAX_ERROR = "syntax_error"       # 语法错误
    SEMANTIC_ERROR = "semantic_error"   # 语义错误（表/列不存在）
    RUNTIME_ERROR = "runtime_error"     # 运行时错误
    TIMEOUT_ERROR = "timeout_error"     # 超时错误
    PERMISSION_ERROR = "permission_error"  # 权限错误
    UNKNOWN = "unknown"


@dataclass
class StructuredError:
    """结构化的错误信息"""
    error_type: ErrorType               # 错误类型
    original_message: str               # 原始错误消息
    table_name: str = None              # 涉及的表名（如果有）
    column_name: str = None             # 涉及的列名（如果有）
    suggested_fix: str = None           # 建议的修复方式（可选）

    def to_prompt_format(self) -> str:
        """
        将错误格式化为适合 LLM 阅读的文本

        Returns:
            str: 格式化后的错误信息
        """
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
    success: bool                                   # 是否成功
    sql: str                                        # 执行的 SQL
    result_data: Any = None                         # 查询结果
    execution_time: float = None                    # 执行时间（秒）
    row_count: int = None                           # 返回行数
    error: StructuredError = None                   # 错误信息（如果失败）
    explain_plan: str = None                        # 执行计划（如果使用了 EXPLAIN）


class SQLExecutor:
    """
    SQL 执行器 - 安全执行和错误处理

    工作流程:
    ┌──────────────┐
    │ SQL 语句      │
    └──────┬───────┘
           │
    ┌──────▼───────┐
    │ EXPLAIN 验证  │  (预检查语法)
    └──────┬───────┘
           │
    ┌──────▼───────┐
    │ 执行 SQL      │
    └──────┬───────┘
           │
    ┌──────┴───────┐
    │ 成功？       │
    │  ├─ 是 → 返回结果
    │  └─ 否 → 结构化错误
    └──────────────┘

    Attributes:
        db_connector: 数据库连接器
        default_timeout: 默认超时时间（秒）
    """

    def __init__(self, db_connector=None, default_timeout: int = 30):
        """
        初始化执行器

        Args:
            db_connector: 数据库连接器实例
            default_timeout: 默认查询超时时间
        """
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
        pass

    def execute(self, sql: str, timeout: int = None) -> ExecutionResult:
        """
        执行 SQL 查询

        Args:
            sql: SQL 语句
            timeout: 超时时间（秒），默认使用 class 级别的设置

        Returns:
            ExecutionResult: 执行结果

        TODO:
        - 记录开始时间
        - 执行查询
        - 记录结束时间和结果
        - 捕获异常并转换为 StructuredError
        """
        pass

    def _parse_error(self, error_msg: str) -> StructuredError:
        """
        解析原始错误消息，提取结构化信息

        Args:
            error_msg: 原始错误消息

        Returns:
            StructuredError: 结构化的错误信息
        """
        pass


class ErrorHandler:
    """
    错误处理器 - 错误分类和修正建议

    用于将原始数据库错误转换为 LLM 可读的格式
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
        # 简单的关键词匹配
        error_upper = error_msg.upper()

        if 'SYNTAX' in error_upper or 'near' in error_msg.lower():
            return ErrorType.SYNTAX_ERROR
        elif 'no such table' in error_lower:
            return ErrorType.SEMANTIC_ERROR
        elif 'no such column' in error_lower:
            return ErrorType.SEMANTIC_ERROR
        elif 'timeout' in error_lower:
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
        pass

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
            ErrorType.SEMANTIC_ERROR: "请检查表名或列名是否存在，注意大小写。",
            ErrorType.TIMEOUT_ERROR: "查询超时，请简化查询或增加超时时间。",
            ErrorType.PERMISSION_ERROR: "没有执行此查询的权限，请检查用户权限。",
        }
        return fixes.get(error.error_type, "请检查 SQL 语句。")