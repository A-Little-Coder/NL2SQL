"""SQL 字段解析（sqlglot）

解析 final_sql 的 SELECT 输出列与引用的底层数据列（含聚合函数内字段、列别名），
供脱敏节点判断哪些结果列对应黑名单字段。

核心函数：
- parse_select_outputs(sql): 返回每个输出列的 alias 与引用的 (table, column) 列表
- output_columns_to_mask(sql, is_denied_fn): 返回需脱敏的输出列 alias 列表
"""

from typing import Callable, Dict, List

import sqlglot
from sqlglot import exp


def _extract_from_tables(parsed) -> List[str]:
    """提取 SQL 涉及的表名（FROM/JOIN）"""
    return [tbl.name for tbl in parsed.find_all(exp.Table)]


def _guess_table(from_tables: List[str]) -> str:
    """单表场景下补全列的表名；多表时留空（交由 is_denied 通配规则判断）"""
    return from_tables[0] if len(from_tables) == 1 else ""


def parse_select_outputs(sql: str) -> List[Dict]:
    """解析 SELECT 输出列

    Args:
        sql: SELECT 语句

    Returns:
        [{"alias": str, "refs": [(table, column), ...]}, ...]
        - alias: 输出列名（别名或列名，对应结果 dict 的 key）
        - refs: 该输出列引用的底层数据列（含聚合函数内的列），table 缺省时按单表补全
        解析失败返回空列表（调用方应保守脱敏）。
    """
    try:
        parsed = sqlglot.parse_one(sql, dialect="sqlite")
    except Exception:
        return []

    from_tables = _extract_from_tables(parsed)
    outputs: List[Dict] = []
    for proj in parsed.expressions:
        alias = proj.alias_or_name
        refs: List[tuple] = []
        for col in proj.find_all(exp.Column):
            t = col.table or ""
            refs.append((t, col.name))
        # table 为空时用 FROM 表补全（单表场景）
        refs = [(t if t else _guess_table(from_tables), c) for t, c in refs]
        outputs.append({"alias": alias, "refs": refs})
    return outputs


def output_columns_to_mask(
    sql: str, is_denied_fn: Callable[[str, str], bool]
) -> List[str]:
    """返回需脱敏的输出列 alias 列表

    Args:
        sql: SELECT 语句
        is_denied_fn: (table, column) -> bool，字段是否在黑名单

    Returns:
        需脱敏的输出列 alias 列表（任一引用列命中黑名单即脱敏）
    """
    outputs = parse_select_outputs(sql)
    if not outputs:
        return []
    masked: List[str] = []
    for o in outputs:
        alias = o["alias"]
        refs = o["refs"]
        if not refs:
            continue
        if any(is_denied_fn(t, c) for t, c in refs):
            masked.append(alias)
    return masked
