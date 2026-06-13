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


# 决策 51：错误严重程度（轻 → 重），用于全失败分支按等级排序逐个修复
ERROR_SEVERITY: Dict["ErrorType", int] = {}  # 在类定义后填充

# 决策 51：不可修复错误类型，SmartFix 跳过 LLM 调用
UNFIXABLE_ERRORS: set = set()  # 在类定义后填充


def _init_error_severity():
    """模块加载时初始化错误等级映射（避免类前向引用问题）"""
    ERROR_SEVERITY.update({
        ErrorType.SEMANTIC_ERROR: 1,   # 表名/列名错，LLM 最容易修对
        ErrorType.SYNTAX_ERROR: 2,
        ErrorType.UNKNOWN: 3,
        ErrorType.TIMEOUT_ERROR: 4,    # 数据/查询问题，LLM 难修
        ErrorType.RUNTIME_ERROR: 5,    # 数据问题，LLM 难修
        ErrorType.PERMISSION_ERROR: 6, # 权限问题，LLM 修不了
    })
    UNFIXABLE_ERRORS.update({
        ErrorType.TIMEOUT_ERROR,
        ErrorType.RUNTIME_ERROR,
        ErrorType.PERMISSION_ERROR,
    })


_init_error_severity()


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

# SQL_FIX_PROMPT 已迁移至 src/execution/prompts.py
from src.execution.prompts import SQL_FIX_PROMPT
from utils.llm_client import parse_json, stream_with_sse


def _format_fix_history(history: List[Dict[str, Any]]) -> str:
    """构造 fix_history 提示段（决策 51）

    Args:
        history: [{"round": int, "sql": str, "error": str}, ...]

    Returns:
        str: 给 LLM 的历史提示文本（空 history 时返回 ""）
    """
    if not history:
        return ""
    lines = ["\n历次修复尝试（请基于这些历史，避免再犯同样的错）："]
    for h in history:
        lines.append(f"- 第 {h.get('round', '?')} 轮:")
        lines.append(f"  SQL: {h.get('sql', '')}")
        lines.append(f"  错误: {h.get('error', '')}")
    lines.append("")
    return "\n".join(lines)


class SQLFixLoop:
    """
    SQL SmartFix 修复循环（决策 51：单候选 ≤3 轮 + fix_history）

    重大变更（决策 51）：
    - max_retries 默认改为 3 轮（原 2 轮）
    - 每轮 prompt 携带 fix_history（历次 SQL + 错误），避免反复犯错
    - _try_fix 入口过滤 UNFIXABLE_ERRORS（TIMEOUT/RUNTIME/PERMISSION）不调 LLM
    - 仅修复 1 个候选（外部调用方决定，本类不再循环调用 5 候选）

    Attributes:
        executor: SQLExecutor 实例
        llm_client: LLM 客户端
        max_retries: 最大重试次数（默认 3）
    """

    def __init__(self, executor: SQLExecutor, llm_client=None, max_retries: int = 3):
        self.executor = executor
        self.llm_client = llm_client
        self.max_retries = max_retries

    def run(self, sql: str, user_query: str, schema_text: str = "",
            initial_error: Optional[StructuredError] = None) -> Dict[str, Any]:
        """SmartFix：单候选最多 max_retries 轮修复（决策 51）

        Args:
            sql: 初始 SQL（来自评分阶段选中的候选）
            user_query: 原始用户查询
            schema_text: schema 描述文本
            initial_error: 进入 SmartFix 时已知的错误（来自 ExecuteAll；为 None 时先执行一次）

        Returns:
            Dict:
                - result: ExecutionResult（成功的结果 或 最后一次失败的结果）
                - fix_history: List[Dict] [{round, sql, error}]
                - fix_rounds_used: int
                - fix_failed: bool
                - last_error: Optional[str]
        """
        fix_history: List[Dict[str, Any]] = []
        current_sql = sql
        current_error = initial_error
        last_result: Optional[ExecutionResult] = None

        # 不可修类型直接短路（不调 LLM）
        if current_error is not None and current_error.error_type in UNFIXABLE_ERRORS:
            logger.info(f"SmartFix 短路：错误类型 {current_error.error_type.value} 不可修复")
            return {
                "result": ExecutionResult(
                    success=False, sql=current_sql, error=current_error,
                ),
                "fix_history": [],
                "fix_rounds_used": 0,
                "fix_failed": True,
                "last_error": current_error.original_message,
            }

        for round_idx in range(1, self.max_retries + 1):
            # 修复（用上一轮的 SQL + 错误 + 历次记录）
            if current_error is None:
                # 首轮且无 initial_error：先执行一次拿到错误
                pre_result = self.executor.execute(current_sql)
                if pre_result.success:
                    return {
                        "result": pre_result,
                        "fix_history": fix_history,
                        "fix_rounds_used": 0,
                        "fix_failed": False,
                        "last_error": None,
                    }
                current_error = pre_result.error
                last_result = pre_result
                # 进入下面修复流程
                if current_error.error_type in UNFIXABLE_ERRORS:
                    return {
                        "result": pre_result,
                        "fix_history": fix_history,
                        "fix_rounds_used": 0,
                        "fix_failed": True,
                        "last_error": current_error.original_message,
                    }

            fixed_sql = self._try_fix(current_sql, current_error, user_query,
                                       schema_text, fix_history)
            if not fixed_sql or fixed_sql == current_sql:
                # LLM 没给出新 SQL，提前退出（剩余轮次不可能改善）
                logger.warning(f"SmartFix 第 {round_idx} 轮 LLM 未提供新 SQL，提前结束")
                break

            # 执行新 SQL
            result = self.executor.execute(fixed_sql)
            current_sql = fixed_sql
            last_result = result

            # SSE 推送
            try:
                from src.api.streaming import emit_safe
                emit_safe("smart_fix_round", {
                    "round": round_idx,
                    "sql": fixed_sql,
                    "error": result.error.original_message if result.error else None,
                    "success": result.success,
                })
            except Exception:
                pass

            if result.success:
                logger.info(f"SmartFix 第 {round_idx} 轮成功")
                return {
                    "result": result,
                    "fix_history": fix_history + [{
                        "round": round_idx, "sql": fixed_sql, "error": None,
                    }],
                    "fix_rounds_used": round_idx,
                    "fix_failed": False,
                    "last_error": None,
                }

            # 失败：追加到 history，进入下一轮
            err_msg = result.error.original_message if result.error else "unknown"
            fix_history.append({
                "round": round_idx, "sql": fixed_sql, "error": err_msg,
            })
            current_error = result.error

        # 所有轮次都失败
        last_err_msg = (
            last_result.error.original_message
            if last_result and last_result.error else "unknown"
        )
        return {
            "result": last_result if last_result else ExecutionResult(
                success=False, sql=current_sql,
            ),
            "fix_history": fix_history,
            "fix_rounds_used": len(fix_history),
            "fix_failed": True,
            "last_error": last_err_msg,
        }

    def _try_fix(self, sql: str, error: StructuredError, user_query: str,
                 schema_text: str,
                 fix_history: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
        """尝试用 LLM 修正 SQL（决策 51：跳过不可修错误 + 带 fix_history）

        Args:
            sql: 失败的 SQL
            error: 错误信息
            user_query: 原始查询
            schema_text: schema 描述
            fix_history: 历次修复记录（None 表示首轮）

        Returns:
            Optional[str]: 修正后的 SQL，无法修正时返回 None
        """
        if not self.llm_client or not error:
            return None

        # 决策 51：不可修类型直接跳过 LLM
        if error.error_type in UNFIXABLE_ERRORS:
            logger.info(f"_try_fix 跳过 LLM：错误类型 {error.error_type.value} 不可修复")
            return None

        try:
            messages = SQL_FIX_PROMPT.format_messages(
                user_query=user_query,
                sql=sql,
                error_info=error.to_prompt_format(),
                schema_text=schema_text or "(未提供 schema)",
                fix_history_section=_format_fix_history(fix_history or []),
            )
            raw = stream_with_sse(self.llm_client.stream(messages, as_json=True, temperature=0.0, run_name="exec-smartfix"))
            result = parse_json(raw)
            fixed = result.get("sql", "").strip()
            return fixed if fixed else None
        except Exception as e:
            logger.error(f"LLM 修正失败: {e}")
            return None

    # ------------------------------------------------------------------
    # LangGraph 子图接口（已废弃，保留方法签名向后兼容）
    # ------------------------------------------------------------------
    def build_graph(self):
        """已废弃（决策 51）：SmartFix 改为由 Decision 子图直接调用 run() 方法

        保留以兼容现有调用方（如有），返回空 graph。
        """
        from src.execution.execution_graph import build_execution_graph
        return build_execution_graph(self)
