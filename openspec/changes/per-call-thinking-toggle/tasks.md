## 1. LLMClient 新增 per-call thinking 参数

- [x] 1.1 `utils/llm_client.py`:四个公开方法(invoke / stream / ainvoke / astream)签名新增 `thinking: Optional[bool] = None` 参数
- [x] 1.2 `utils/llm_client.py`:`_bind_runtime` 方法新增 `thinking` 参数,当 `thinking is not None` 时通过 `bind(extra_body={"enable_thinking": bool(thinking)})` 覆盖构造时的 `extra_body`;`thinking=None` 时不 bind extra_body(沿用构造时默认值)
- [x] 1.3 `tests/utils/test_llm_client_new.py`:新增测试用例验证 `thinking=False` / `thinking=True` / `thinking=None` 三种情况 `_bind_runtime` 的 bind 参数正确
- [x] 1.4 用探测脚本 `scripts/probe_chatopenai_reasoning.py` 验证 `ChatOpenAI.bind(extra_body=...)` 能正确覆盖构造时的 `extra_body`(thinking on→off / off→on 两种方向)—— 通过单元测试断言 `RunnableBinding.kwargs["extra_body"]` 验证
- [x] 1.5 `pytest tests/utils/test_llm_client_new.py -x` 通过(41 passed)

## 2. 3 个简单步骤关闭 thinking

- [x] 2.1 `src/retrieval/information_retrieval.py`:关键词抽取的 LLM 调用加 `thinking=False`
- [x] 2.2 `src/preprocessing/schema_graph_builder.py`:JOIN 推断的 LLM 调用加 `thinking=False`
- [x] 2.3 `src/verification/answerability.py`:可回答性检查的 LLM 调用加 `thinking=False`
- [x] 2.4 `pytest tests/retrieval tests/preprocessing tests/verification -x` 通过

## 3. 删除旧决策代码残留

- [x] 3.1 全局 grep 确认 `llm_final_decision` / `decide()` / `group_by_result` / `find_majority_group` / `select_fastest_from_group` 无外部调用者(除 self_consistency.py 自身和测试文件外)
- [x] 3.2 `src/decision/self_consistency.py`:删除 `llm_final_decision()` 方法
- [x] 3.3 `src/decision/self_consistency.py`:删除 `decide()` 方法
- [x] 3.4 `src/decision/self_consistency.py`:删除 `group_by_result()` / `find_majority_group()` / `select_fastest_from_group()` / `compute_result_hash()` 四个只被 `decide()` 调用的方法
- [x] 3.5 `src/decision/self_consistency.py`:删除 `from prompts import LLM_FINAL_DECISION_PROMPT` import
- [x] 3.6 `src/decision/prompts.py`:删除 `LLM_FINAL_DECISION_PROMPT` 定义及注释
- [x] 3.7 `tests/decision/test_self_consistency.py`:删除 `llm_final_decision` 和 `decide()` 相关测试用例,重写为保留方法的测试
- [x] 3.8 `pytest tests/decision -x` 通过(32 passed)

## 4. 全量回归 + 提交

- [x] 4.1 `pytest -x` 全量通过(467 passed,2 个 pre-existing 失败已忽略)
- [x] 4.2 commit:`feat(llm): per-call thinking 切换 + 清理旧决策残留 + 3 步关闭 thinking`
