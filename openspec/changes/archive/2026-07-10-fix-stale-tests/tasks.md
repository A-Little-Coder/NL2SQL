## 1. test_db_pool 修复（Globals memory_dir）

- [x] 1.1 `tests/api/test_db_pool.py`：`_make_pool` 的 `Globals` 构造补 `memory_dir="/fake/memory"`；grep 该文件所有 `Globals(` 构造点，逐一补 `memory_dir`
- [x] 1.2 运行 `python -m pytest tests/api/test_db_pool.py -v`，9 个失败归零、全部通过

## 2. test_schema_doc_generator 修复（格式同步）

- [x] 2.1 `tests/preprocessing/test_schema_doc_generator.py`：
  - `test_format_column_document_basic`：去掉 `assert "订单总额" in doc`（desc 取 column_description 时 column_name 不在 doc），改为断言 `"orders | total_amt | 订单总金额，包含运费" in doc`
  - `test_format_column_document_boost`：改为断言 `doc == "t | x | 销量"`（去 boost 语义，可重命名为 `test_format_column_document_no_boost`），去掉 `endswith` boost 断言
  - 复核第三个失败用例同步修
- [x] 2.2 `src/preprocessing/schema_doc_generator.py` 模块 docstring（line 21 附近）：去掉过时 boost 描述，与 `format_column_document` 实现一致
- [x] 2.3 运行 `python -m pytest tests/preprocessing/test_schema_doc_generator.py -v`，3 个失败归零、全部通过

## 3. 验证

- [x] 3.1 全量 `python -m pytest tests/ --ignore=tests/e2e_live.py --ignore=tests/e2e_live_with_memory.py`：原 12 失败归零（剩余应为 0 失败，除非有 live 依赖）-- lead 统一跑（658 passed, 0 failed）
- [x] 3.2 `openspec validate fix-stale-tests --strict` 通过 -- lead 统一跑
