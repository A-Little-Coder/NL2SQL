# 调用 NL2SQL 问数 API 的最小脚本（决策 50 真流式版）
#
# 用法：
#   1. 先启动服务：python run_api.py --db_id california_schools
#   2. 运行本脚本：python tests/api_client_demo.py
#
# 特性：
#   - 设置 read=None，配合服务端心跳，永不超时
#   - 按 SSE 事件类型分类打印（stage / llm_thinking 累加 / 业务事件 / result）

import json
import sys
import os
import httpx

if sys.platform == "win32":
    os.system("chcp 65001 > nul 2>&1")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://localhost:8000"

payload = {
    "user_id": "demo_user",
    "session_id": "demo_session_005",
    "db_id": "california_schools",
    # "query": "帮我删库",
    # "query": "查询所有学校的平均sat成绩",
    "query": "查询sat成绩最高的学校和洛杉矶的学校数量",
}

# 决策 50：服务端走真流式 + 心跳，客户端读超时设为 None
timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)

# 累积 llm_thinking 文本（按 node 分组），每个 node 首次打印 [思考|node] 前缀
_thinking_node = None

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
        # SSE 注释行：心跳
        if line.startswith(":"):
            # print(line.strip(), flush=True)  # 如需观察心跳取消注释
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
            # 切换 node 时换行 + 前缀；同 node 内连续输出
            if node != _thinking_node:
                if _thinking_node is not None:
                    print()
                print(f"[思考|{node}] ", end="", flush=True)
                _thinking_node = node
            print(text, end="", flush=True)
            continue

        # 业务事件：先把思考链结尾换行
        if _thinking_node is not None:
            print()
            _thinking_node = None

        if etype == "stage":
            print(f"[stage] {data.get('node')} {data.get('status')}")
        elif etype == "cache_check":
            print(f"[cache_check] hit={data.get('hit')} src={data.get('source')} "
                  f"conf={data.get('confidence')}")
        elif etype == "keywords":
            groups = data.get("groups", [])
            print(f"[keywords] {len(groups)} 组: "
                  + ", ".join(g.get("name", "?") for g in groups))
        elif etype == "schema_recall":
            groups = data.get("groups", [])
            print(f"[schema_recall] {len(groups)} 组")
            for g in groups:
                print(f"   - {g.get('name')}: {g.get('top_columns', [])[:5]}...")
        elif etype == "answerability":
            print(f"[answerability] answerable={data.get('answerable')} "
                  f"reason={data.get('reason', '')[:80]}...")
        elif etype == "sql_candidates":
            cands = data.get("candidates", [])
            print(f"[sql_candidates] {len(cands)} 候选")
            for c in cands:
                print(f"   - {c.get('id')}: {c.get('sql', '')[:100]}...")
        elif etype == "execution":
            print(f"[execution] {data.get('candidate_id')} "
                  f"success={data.get('success')} rows={data.get('rows')}")
        elif etype == "final_decision":
            print(f"[final_decision] selected={data.get('selected_id')} "
                  f"reason={(data.get('reason') or '')[:80]}...")
        elif etype == "result":
            sql = data.get("sql", "")
            result = data.get("result")
            print(f"\n[result] sql = {sql}")
            if isinstance(result, list):
                print(f"[result] rows = {len(result)}, sample = {result[:3]}")
            else:
                print(f"[result] data = {result}")
        elif etype == "error":
            print(f"[error] {data}")
        elif etype == "done":
            print(f"[done] has_result={data.get('has_result')}")
        else:
            print(f"[{etype}] {data}")
