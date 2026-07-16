"""SQL 字段解析单测（task 5.1）"""

from src.permission.sql_parser import parse_select_outputs, output_columns_to_mask


def test_plain_columns():
    out = parse_select_outputs("SELECT name, salary FROM employees")
    assert [o["alias"] for o in out] == ["name", "salary"]
    assert out[1]["refs"] == [("employees", "salary")]


def test_aliased_column():
    out = parse_select_outputs("SELECT salary AS s FROM employees")
    assert out[0]["alias"] == "s"
    assert ("employees", "salary") in out[0]["refs"]


def test_aggregate_refs_inner_column():
    out = parse_select_outputs(
        "SELECT dept, AVG(salary) FROM employees GROUP BY dept"
    )
    avg = [o for o in out if any(c == "salary" for _, c in o["refs"])]
    assert avg, "AVG(salary) 应引用 salary 列"
    assert ("employees", "salary") in avg[0]["refs"]


def test_table_inferred_single_table():
    out = parse_select_outputs("SELECT salary FROM employees")
    assert ("employees", "salary") in out[0]["refs"]


def test_output_columns_to_mask_plain():
    masked = output_columns_to_mask(
        "SELECT name, salary FROM employees", lambda t, c: c == "salary"
    )
    assert masked == ["salary"]


def test_output_columns_to_mask_aggregate():
    masked = output_columns_to_mask(
        "SELECT dept, AVG(salary) FROM employees GROUP BY dept",
        lambda t, c: c == "salary",
    )
    assert len(masked) == 1  # 只脱敏 AVG(salary)，dept 不脱


def test_output_columns_to_mask_none():
    masked = output_columns_to_mask(
        "SELECT name FROM employees", lambda t, c: c == "salary"
    )
    assert masked == []


def test_parse_invalid_returns_empty():
    assert parse_select_outputs("this is not sql !!!") == []
