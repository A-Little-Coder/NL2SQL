# HistoryCache 三机制端到端验证客户端
#
# 配合 harden-history-cache change 使用：验证
#   1) 写入门控：成功执行才写入会话历史
#   2) value_rewrite：命中后改写 cached_sql 中的值参数
#   3) cache_confirm：反问用户，确认复用 / 拒绝回退
#
# 用法：
#   1. 启动服务：python run_api.py --db_id california_schools
#   2. 首次查询：python tests/verify_history_cache.py "统计 SAT 平均阅读成绩大于 500 的学校数量"
#   3. 触发命中+反问：python tests/verify_history_cache.py "统计 SAT 平均阅读成绩大于 600 的学校数量"
#   4. 确认复用：python tests/verify_history_cache.py --resume "复用"
#   5. 拒绝回退：python tests/verify_history_cache.py --resume "不"
#
# 关键事件高亮：cache_check / value_rewrite / cache_confirm / clarification / result

import argparse
import json
import os
import sys

import httpx

if sys.platform == "win32":
    os.system("chcp 65001 > nul 2>&1")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://localhost:8000"

# 贯穿一次验证全流程的固定标识；如需重跑干净场景，用 --session 换一个
DEFAULT_SESSION = "verify_hc_001"
DEFAULT_USER = "verify_user"
DEFAULT_DB = "california_schools"

# 本次开发最关心的几个事件类型（高亮打印）
HIGHLIGHT = {"cache_check", "value_rewrite", "cache_confirm", "clarification", "result", "done"}

_thinking_node = None


def _flush_thinking():
    global _thinking_node
    if _thinking_node is not None:
        print()
        _thinking_node = None


def send(query: str, resume: str, session: str, user: str, db: str):
    global _thinking_node
    payload = {"user_id": user, "session_id": session, "db_id": db}
    if resume is not None:
        payload["resume"] = resume
        payload["query"] = ""  # resume 请求 query 可留空
        print(f"\n>>> [RESUME] session={session} resume={resume!r}\n")
    else:
        payload["query"] = query
        print(f"\n>>> [QUERY] session={session} query={query!r}\n")

    timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    with httpx.stream("POST", f"{BASE_URL}/api/v1/query",
                      json=payload, timeout=timeout) as r:
        if r.status_code != 200:
            print(f"HTTP {r.status_code}")
            print(r.read().decode("utf-8", errors="replace"))
            sys.exit(1)

        for raw in r.iter_lines():
            if not raw:
                continue
            line = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
            if line.startswith(":"):  # 心跳
                continue
            if not line.startswith("data:"):
                continue
            try:
                evt = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue

            etype = evt.get("type")
            data = evt.get("data", {})

            if etype == "llm_thinking":
                node = data.get("node", "?")
                text = data.get("text", "")
                if node != _thinking_node:
                    _flush_thinking()
                    print(f"[思考|{node}] ", end="", flush=True)
                    _thinking_node = node
                print(text, end="", flush=True)
                continue

            _flush_thinking()

            mark = "★" if etype in HIGHLIGHT else " "
            if etype == "stage":
                print(f"[stage] {data.get('node')} {data.get('status')}")
            elif etype == "cache_check":
                print(f"{mark}[cache_check] hit={data.get('hit')} "
                      f"source={data.get('source')} conf={data.get('confidence')} "
                      f"recalled={data.get('recalled')}")
                if data.get("cached_sql"):
                    print(f"           cached_sql = {data.get('cached_sql')}")
            elif etype == "value_rewrite":
                print(f"{mark}[value_rewrite] changed={data.get('changed')} "
                      f"reason={data.get('reason')}")
                print(f"           historical_query = {data.get('historical_query')}")
                print(f"           user_query       = {data.get('user_query')}")
                print(f"           cached_sql       = {data.get('cached_sql')}")
                print(f"           adjusted_sql     = {data.get('adjusted_cached_sql')}")
            elif etype == "cache_confirm":
                print(f"{mark}[cache_confirm] approved={data.get('approved')} "
                      f"user_choice={data.get('user_choice')!r}")
            elif etype == "clarification":
                print(f"{mark}[clarification] awaiting_answer={data.get('awaiting_answer')}")
                print(f"           round={data.get('round')}")
                print("           question:")
                for ln in str(data.get("question", "")).splitlines():
                    print(f"             | {ln}")
            elif etype == "result":
                print(f"{mark}[result] sql = {data.get('sql')}")
                res = data.get("result")
                if isinstance(res, list):
                    print(f"           rows = {len(res)}, sample = {res[:3]}")
                else:
                    print(f"           data = {res}")
            elif etype == "done":
                print(f"{mark}[done] has_result={data.get('has_result')} "
                      f"awaiting_clarification={data.get('awaiting_clarification')} "
                      f"fix_failed={data.get('fix_failed')} "
                      f"decision_path={data.get('decision_path')!r}")
            elif etype == "error":
                print(f"{mark}[error] {data}")
            else:
                print(f"[{etype}] {data}")


def main():
    ap = argparse.ArgumentParser(description="HistoryCache 三机制验证客户端")
    ap.add_argument("query", nargs="?", default="", help="自然语言查询（resume 模式可留空）")
    ap.add_argument("--resume", default=None, help="反问恢复：填用户回答，如 '复用' / '不'")
    ap.add_argument("--session", default=DEFAULT_SESSION, help=f"会话 ID（默认 {DEFAULT_SESSION}）")
    ap.add_argument("--user", default=DEFAULT_USER, help=f"用户 ID（默认 {DEFAULT_USER}）")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"数据库 ID（默认 {DEFAULT_DB}）")
    args = ap.parse_args()

    if args.resume is None and not args.query:
        ap.error("非 resume 模式必须提供 query")

    send(args.query, args.resume, args.session, args.user, args.db)


if __name__ == "__main__":
    main()
