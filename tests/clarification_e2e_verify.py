"""反问机制端到端接口验证脚本（决策 9-15）

通过 HTTP 接口验证反问机制的真实端到端行为（真实 LLM）：
  场景 1：拒答（"帮我删库" → REJECT，不执行 SQL）
  场景 2：单意图执行（清晰查询 → EXECUTE → 出 SQL）
  场景 3：反问 interrupt/resume（歧义查询 → 触发反问 → 回答后继续）
  场景 4：多意图（复合查询 → 分解多子查询 → 汇总）

使用：
  1. 先启动服务：python run_api.py --db_id california_schools
  2. 另开终端：python tests/clarification_e2e_verify.py
  3. 可选参数：--base-url http://localhost:8000 --db-id california_schools --session <会话id>

注意：真实调用 LLM，每个场景耗时数秒到数十秒。反问场景需要真实触发，依赖 LLM 判断。
"""

import argparse
import json
import sys
import uuid

import httpx


BASE_URL = "http://localhost:8000"
DB_ID = "california_schools"


# ---------------------------------------------------------------------------
# SSE 流式请求工具
# ---------------------------------------------------------------------------
def stream_query(base_url, payload, timeout=None):
    """发起一次 SSE 查询请求，yield 所有事件 dict。

    返回生成器，每个元素是 {"type":..., "data":...}。
    """
    if timeout is None:
        timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)

    with httpx.stream("POST", f"{base_url}/api/v1/query",
                      json=payload, timeout=timeout) as r:
        if r.status_code != 200:
            body = r.read().decode("utf-8", errors="replace")
            yield {"type": "__http_error__", "data": {"status": r.status_code, "body": body}}
            return
        for raw in r.iter_lines():
            if not raw:
                continue
            line = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
            if line.startswith(":") or not line.startswith("data:"):
                continue
            try:
                evt = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            yield evt


def collect_events(base_url, payload, timeout=None):
    """收集一次请求的全部事件，返回 list。"""
    return list(stream_query(base_url, payload, timeout))


def find_event(events, etype):
    """找第一个指定类型的事件，找不到返回 None。"""
    for e in events:
        if e.get("type") == etype:
            return e
    return None


def print_event_brief(evt, indent="  "):
    """简要打印一个事件（不打印 llm_thinking 细节，避免刷屏）。"""
    etype = evt.get("type")
    data = evt.get("data", {})
    if etype == "llm_thinking":
        return  # 跳过思考链细节
    if etype == "stage":
        print(f"{indent}[stage] {data.get('node')} {data.get('status')}")
    elif etype == "clarification":
        print(f"{indent}[clarification] 反问: {data.get('question', '')}")
        print(f"{indent}            歧义: {data.get('ambiguities', [])}")
    elif etype == "cache_check":
        print(f"{indent}[cache_check] hit={data.get('hit')} src={data.get('source')}")
    elif etype == "keywords":
        groups = data.get("groups", [])
        print(f"{indent}[keywords] {len(groups)} 组")
    elif etype == "answerability":
        print(f"{indent}[answerability] {data.get('answerable')}: {str(data.get('reason',''))[:60]}")
    elif etype == "sql_candidates":
        cands = data.get("candidates", [])
        print(f"{indent}[sql_candidates] {len(cands)} 候选")
        for c in cands[:2]:
            print(f"{indent}   - {c.get('sql','')[:80]}")
    elif etype == "execution":
        print(f"{indent}[execution] {data.get('candidate_id')} success={data.get('success')} rows={data.get('rows')}")
    elif etype == "final_decision":
        print(f"{indent}[final_decision] path={data.get('decision_path')} sql={str(data.get('selected_sql',''))[:60]}")
    elif etype == "result":
        print(f"{indent}[result] sql={str(data.get('sql',''))[:80]}")
    elif etype == "error":
        print(f"{indent}[error] {data.get('error','')}")
    elif etype == "done":
        print(f"{indent}[done] has_result={data.get('has_result')} awaiting_clarification={data.get('awaiting_clarification')}")
    else:
        print(f"{indent}[{etype}] {str(data)[:80]}")


# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------
def scenario_reject(base_url, db_id, session):
    """场景 1：拒答（写操作 / 越权）"""
    print("\n" + "=" * 70)
    print("场景 1：拒答（帮我删库 → REJECT，不执行 SQL）")
    print("=" * 70)
    payload = {"query": "帮我删库", "session_id": session, "user_id": "verify_user", "db_id": db_id}
    events = collect_events(base_url, payload)
    for e in events:
        print_event_brief(e)

    done = find_event(events, "done")
    error_evt = find_event(events, "error")
    result_evt = find_event(events, "result")

    # 断言：应有 error 事件（拒答原因），无 result，done.has_result=False
    ok = True
    if not error_evt:
        print("  [FAIL] 缺少 error 事件（拒答原因）")
        ok = False
    if result_evt:
        print("  [FAIL] 拒答场景不应有 result 事件")
        ok = False
    if done and done["data"].get("has_result"):
        print("  [FAIL] 拒答 done.has_result 应为 False")
        ok = False
    print(f"  => {'PASS' if ok else 'FAIL'}")
    return ok


def scenario_single_execute(base_url, db_id, session):
    """场景 2：单意图执行"""
    print("\n" + "=" * 70)
    print("场景 2：单意图执行（清晰查询 → EXECUTE → 出 SQL）")
    print("=" * 70)
    payload = {
        "query": "查询洛杉矶（Los Angeles）的公立学校总数",
        "session_id": session, "user_id": "verify_user", "db_id": db_id,
    }
    events = collect_events(base_url, payload)
    for e in events:
        print_event_brief(e)

    result_evt = find_event(events, "result")
    done = find_event(events, "done")
    ok = True
    if not result_evt:
        print("  [FAIL] 缺少 result 事件")
        ok = False
    else:
        print(f"  最终 SQL: {result_evt['data'].get('sql','')}")
    if done and done["data"].get("awaiting_clarification"):
        print("  [FAIL] 单意图不应触发 awaiting_clarification")
        ok = False
    print(f"  => {'PASS' if ok else 'FAIL'}")
    return ok


def scenario_clarify_resume(base_url, db_id, session):
    """场景 3：反问 interrupt/resume

    用一个有歧义的查询，看是否触发反问；若触发则回答后 resume。
    """
    print("\n" + "=" * 70)
    print("场景 3：反问 interrupt/resume（歧义查询 → 触发反问 → 回答后继续）")
    print("=" * 70)
    payload = {
        "query": "查一下苹果的数据",  # "苹果" + "数据" 较模糊，可能触发反问
        "session_id": session, "user_id": "verify_user", "db_id": db_id,
    }

    # 首次请求
    print("\n--- 首次请求 ---")
    first_events = collect_events(base_url, payload)
    for e in first_events:
        print_event_brief(e)

    clarify_evt = find_event(first_events, "clarification")
    done = find_event(first_events, "done")

    if not clarify_evt:
        # 没触发反问——可能 LLM 直接 execute/reject 了，不算失败，但要说明
        print("\n  [INFO] 本次未触发反问（LLM 直接裁决为 execute/reject）")
        result_evt = find_event(first_events, "result")
        if result_evt or (done and done["data"].get("has_result") is False):
            print("  => PASS（未触发反问但流程正常完成）")
            return True
        print("  => FAIL（既无反问也无结果）")
        return False

    # 触发了反问 → resume
    print(f"\n  触发反问: {clarify_evt['data'].get('question','')}")
    print("\n--- resume 请求（用户回答）---")
    resume_payload = {
        "query": "",  # resume 时 query 可空
        "session_id": session,  # 同一 session（thread_id）才能恢复
        "user_id": "verify_user",
        "db_id": db_id,
        "resume": "查 Apple 公司相关的学校名称",
    }
    resume_events = collect_events(base_url, resume_payload)
    for e in resume_events:
        print_event_brief(e)

    result_evt = find_event(resume_events, "result")
    done2 = find_event(resume_events, "done")
    ok = True
    if not result_evt:
        # resume 后可能再次反问或拒答，只要 done 正常即算通过
        if not done2:
            print("  [FAIL] resume 后缺少 done 事件")
            ok = False
        else:
            print("  [INFO] resume 后未直接出 result（可能再次反问/拒答），流程正常")
    else:
        print(f"  最终 SQL: {result_evt['data'].get('sql','')}")
    print(f"  => {'PASS' if ok else 'FAIL'}")
    return ok


def scenario_multi_intent(base_url, db_id, session):
    """场景 4：多意图（复合查询 → 分解多子查询 → 汇总）

    用一个明确的复合查询。注意：是否触发多意图取决于 LLM 判断，
    若 LLM 判为单意图，本场景记为 INFO（不算失败）。
    """
    print("\n" + "=" * 70)
    print("场景 4：多意图（查学校总数和平均入学人数 → 分解 → 汇总）")
    print("=" * 70)
    payload = {
        "query": "查询洛杉矶的学校总数，以及这些学校的平均入学人数",
        "session_id": session, "user_id": "verify_user", "db_id": db_id,
    }
    events = collect_events(base_url, payload)
    for e in events:
        print_event_brief(e)

    done = find_event(events, "done")
    final_decision = find_event(events, "final_decision")
    ok = True

    # 检查是否走了多意图路径（final_decision 带 multi_intent 标记）
    is_multi = final_decision and final_decision["data"].get("multi_intent")
    if is_multi:
        print(f"  [INFO] 走了多意图路径: {final_decision['data'].get('subquery_count')} 子查询, "
              f"{final_decision['data'].get('success_count')} 成功")
    else:
        print("  [INFO] LLM 判为单意图（未走多意图路径）")

    if not done:
        print("  [FAIL] 缺少 done 事件")
        ok = False
    print(f"  => {'PASS' if ok else 'FAIL'}")
    return ok


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main():
    global BASE_URL, DB_ID
    parser = argparse.ArgumentParser(description="反问机制端到端接口验证")
    parser.add_argument("--base-url", default=BASE_URL, help="服务地址")
    parser.add_argument("--db-id", default=DB_ID, help="数据库 id")
    parser.add_argument("--session", default=None, help="会话 id（默认随机生成）")
    parser.add_argument("--only", default=None,
                        choices=["1", "2", "3", "4"],
                        help="只跑指定场景（1=拒答 2=单意图 3=反问 4=多意图）")
    args = parser.parse_args()
    BASE_URL = args.base_url
    DB_ID = args.db_id

    # 健康检查
    try:
        r = httpx.get(f"{BASE_URL}/api/v1/health", timeout=10.0)
        if r.status_code != 200:
            print(f"服务未就绪：{BASE_URL}/api/v1/health 返回 {r.status_code}")
            sys.exit(1)
        print(f"服务就绪: {BASE_URL}  db_pool={r.json().get('db_pool')}")
    except Exception as e:
        print(f"无法连接服务 {BASE_URL}: {e}")
        print("请先启动：python run_api.py --db_id california_schools")
        sys.exit(1)

    session = args.session or f"verify_{uuid.uuid4().hex[:8]}"
    print(f"会话: {session}  数据库: {DB_ID}")

    scenarios = {
        "1": ("拒答", lambda: scenario_reject(BASE_URL, DB_ID, session + "_s1")),
        "2": ("单意图", lambda: scenario_single_execute(BASE_URL, DB_ID, session + "_s2")),
        "3": ("反问", lambda: scenario_clarify_resume(BASE_URL, DB_ID, session + "_s3")),
        "4": ("多意图", lambda: scenario_multi_intent(BASE_URL, DB_ID, session + "_s4")),
    }

    results = {}
    keys = [args.only] if args.only else ["1", "2", "3", "4"]
    for k in keys:
        name, fn = scenarios[k]
        try:
            results[k] = fn()
        except Exception as e:
            print(f"\n  场景 {k}（{name}）异常: {e}")
            import traceback
            traceback.print_exc()
            results[k] = False

    # 汇总
    print("\n" + "=" * 70)
    print("验证汇总")
    print("=" * 70)
    for k in keys:
        name = scenarios[k][0]
        status = "PASS" if results.get(k) else "FAIL"
        print(f"  场景 {k}（{name}）: {status}")

    all_pass = all(results.values())
    print(f"\n{'全部通过' if all_pass else '存在失败场景，请检查上方输出'}")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
