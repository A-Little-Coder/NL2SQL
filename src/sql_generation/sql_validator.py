# ============================================================================
# SQL 安全验证器实现
# ============================================================================


import sqlglot
from sqlglot import exp
from typing import Tuple, List


class SQLValidator:
    """SQL 安全验证器 - 基于 sqlglot"""

    # 禁止的危险操作
    DANGEROUS_OPERATIONS = {
        'INSERT', 'UPDATE', 'DELETE', 'DROP',
        'ALTER', 'CREATE', 'TRUNCATE', 'GRANT', 'REVOKE'
    }

    def validate_safety(self, sql: str) -> Tuple[bool, str]:
        """
        验证 SQL 是否包含危险操作

        Args:
            sql: SQL 语句

        Returns:
            Tuple[bool, str]: (是否安全，原因说明)
        """
        try:
            # 使用 sqlglot 解析 SQL
            parsed = sqlglot.parse_one(sql)

            # 获取 SQL 类型
            sql_type = type(parsed).__name__.upper()

            # 检查是否包含危险操作
            if any(op in sql_type for op in ['INSERT', 'UPDATE', 'DELETE']):
                return False, f"检测到数据修改操作：{sql_type}"

            # 检查危险关键字（即使不在开头）
            upper_sql = sql.upper()
            for keyword in self.DANGEROUS_OPERATIONS:
                if keyword in upper_sql:
                    return False, f"检测到危险操作关键字：{keyword}"

            return True, "安全检查通过"

        except Exception as e:
            return False, f"安全检查出错：{str(e)}"

    def validate_syntax(self, sql: str, dialect: str = "sqlite") -> Tuple[bool, str]:
        """
        验证 SQL 语法正确性

        Args:
            sql: SQL 语句
            dialect: SQL 方言 ("sqlite", "mysql", "postgres")

        Returns:
            Tuple[bool, str]: (是否有效，错误信息)
        """
        try:
            # 尝试解析 SQL
            sqlglot.parse_one(sql, read=dialect)
            return True, "语法检查通过"
        except sqlglot.errors.ParseError as e:
            return False, f"语法错误：{str(e)}"
        except Exception as e:
            return False, f"验证出错：{str(e)}"

    def validate(self, sql: str, dialect: str = "sqlite") -> Tuple[bool, str]:
        """
        完整验证（安全和语法）

        Args:
            sql: SQL 语句
            dialect: SQL 方言

        Returns:
            Tuple[bool, str]: (是否有效，错误信息)
        """
        # 先检查安全性
        is_safe, safety_msg = self.validate_safety(sql)
        if not is_safe:
            return False, safety_msg

        # 再检查语法
        is_valid, syntax_msg = self.validate_syntax(sql, dialect)
        return is_valid, syntax_msg