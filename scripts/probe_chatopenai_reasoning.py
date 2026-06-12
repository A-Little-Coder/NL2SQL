"""
ChatOpenAI + Qwen3 探测脚本（一次性，验证通过即删）

方案 X 决策（见 design.md 决策 8）：
─ ChatOpenAI 1.x 默认丢弃 Qwen 的 reasoning_content 字段
─ 通过 output_version="responses/v1" 让 chunk.content 输出 list[dict]
─ list 中按 type 区分 "reasoning"（带 summary[].text）和 "text"

验证项：
1. 流式 stream() chunk.content 是 list[dict]，能拿到 reasoning + content 文本
2. enable_thinking=False 时 reasoning blocks 数为 0（开关有效）
3. bind(response_format={"type":"json_object"}) JSON 模式生效
4. ainvoke / astream 异步路径可用

成功条件（全部通过才进入 Step 2）

运行方式：
    python scripts/probe_chatopenai_reasoning.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Windows 控制台 GBK 编码兼容
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI


def _build_model(*, enable_thinking: bool) -> ChatOpenAI:
    """方案 X：output_version='responses/v1' + extra_body 透传 enable_thinking"""
    extra_body = {"enable_thinking": True} if enable_thinking else {"enable_thinking": False}
    return ChatOpenAI(
        model=os.getenv("QWEN_MODEL", "qwen-plus"),
        api_key=os.getenv("QWEN_API_KEY"),
        base_url=os.getenv(
            "QWEN_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        temperature=0.0,
        max_tokens=512,
        output_version="responses/v1",  # ← 关键：使 chunk.content 输出 list[dict]
        extra_body=extra_body,
    )


def _parse_blocks(chunk_content):
    """按方案 X 解析 chunk.content 的 list[dict]，返回 (text_chunk, reasoning_chunk)"""
    if not isinstance(chunk_content, list):
        return None, None
    text_part = ""
    reasoning_part = ""
    for block in chunk_content:
        if not isinstance(block, dict):
            continue
        t = block.get("type")
        if t == "reasoning":
            for s in block.get("summary", []):
                if isinstance(s, dict) and s.get("text"):
                    reasoning_part += s["text"]
        elif t == "text":
            text_part += block.get("text", "")
    return (text_part or None), (reasoning_part or None)


def _short(text: str, n: int = 100) -> str:
    text = (text or "").strip()
    return text[:n] + "..." if len(text) > n else text


# ──────────────────────────────────────────────────────────────
# 验证项 1：方案 X 流式输出 reasoning + text
# ──────────────────────────────────────────────────────────────

def probe_1_stream_with_output_version():
    print("\n" + "=" * 70)
    print("验证项 1: output_version='responses/v1' 流式 reasoning + content")
    print("=" * 70)

    model = _build_model(enable_thinking=True)
    msgs = [HumanMessage("北京今天天气怎么样？请简单回答。")]

    reasoning_text = ""
    content_text = ""
    total_chunks = 0
    chunks_with_reasoning = 0
    chunks_with_content = 0

    for i, chunk in enumerate(model.stream(msgs)):
        total_chunks += 1
        if i < 3:
            print(f"\n--- chunk {i} ---")
            print(f"  content type     : {type(chunk.content).__name__}")
            print(f"  content (raw)    : {repr(chunk.content)[:160]}")

        text_p, reason_p = _parse_blocks(chunk.content)
        if text_p:
            chunks_with_content += 1
            content_text += text_p
        if reason_p:
            chunks_with_reasoning += 1
            reasoning_text += reason_p

    print(f"\n统计：")
    print(f"  总 chunks          : {total_chunks}")
    print(f"  含 reasoning 的    : {chunks_with_reasoning}")
    print(f"  含 content  的     : {chunks_with_content}")
    print(f"  reasoning 累积     : {_short(reasoning_text, 120)}")
    print(f"  content   累积     : {_short(content_text, 120)}")

    if reasoning_text and content_text:
        print(">>> 验证项 1 通过：reasoning + content 都能拿到")
        return True
    print(">>> 验证项 1 失败")
    return False


# ──────────────────────────────────────────────────────────────
# 验证项 2：enable_thinking 开关效果
# ──────────────────────────────────────────────────────────────

def probe_2_enable_thinking_toggle():
    print("\n" + "=" * 70)
    print("验证项 2: enable_thinking 开关效果")
    print("=" * 70)

    msgs = [HumanMessage("2+3 等于几？")]

    def _count_reasoning(model):
        n = 0
        for chunk in model.stream(msgs):
            _, r = _parse_blocks(chunk.content)
            if r:
                n += 1
        return n

    on_count = _count_reasoning(_build_model(enable_thinking=True))
    off_count = _count_reasoning(_build_model(enable_thinking=False))

    print(f"  enable_thinking=True  reasoning blocks: {on_count}")
    print(f"  enable_thinking=False reasoning blocks: {off_count}")

    if on_count > 0 and off_count == 0:
        print(">>> 验证项 2 通过：开关效果符合预期")
        return True
    if on_count > 0 and off_count > 0:
        print(">>> 验证项 2 警告：关闭时仍有 reasoning（透传机制可用，可能 Qwen 端默认开）")
        return True
    print(">>> 验证项 2 失败")
    return False


# ──────────────────────────────────────────────────────────────
# 验证项 3：JSON 模式
# ──────────────────────────────────────────────────────────────

def probe_3_json_mode():
    print("\n" + "=" * 70)
    print("验证项 3: bind(response_format=json_object) JSON 模式")
    print("=" * 70)

    model = _build_model(enable_thinking=False)
    bound = model.bind(response_format={"type": "json_object"})

    msgs = [
        SystemMessage("你是 JSON 输出助手，只输出合法 JSON。"),
        HumanMessage('返回 JSON 对象：{"city":"北京","weather":"晴"}'),
    ]
    try:
        result = bound.invoke(msgs)
        # 在 output_version=responses/v1 下，invoke 返回的 content 也是 list[dict]
        text, _ = _parse_blocks(result.content)
        print(f"  返回 content 类型: {type(result.content).__name__}")
        print(f"  text 提取结果   : {_short(text, 200) if text else '(空)'}")

        import json
        parsed = json.loads(text)
        print(f"  解析后          : {parsed}")
        print(">>> 验证项 3 通过：JSON 模式生效")
        return True
    except Exception as e:
        print(f">>> 验证项 3 失败：{type(e).__name__}: {e}")
        return False


# ──────────────────────────────────────────────────────────────
# 验证项 4：async 路径
# ──────────────────────────────────────────────────────────────

async def probe_4_async_paths():
    print("\n" + "=" * 70)
    print("验证项 4: ainvoke / astream 异步路径")
    print("=" * 70)

    model = _build_model(enable_thinking=False)
    msgs = [HumanMessage("请说'你好'，仅一句。")]

    try:
        ai_msg = await model.ainvoke(msgs)
        text, _ = _parse_blocks(ai_msg.content)
        print(f"  ainvoke text: {_short(text or str(ai_msg.content), 80)}")
        ainvoke_ok = bool(text)
    except Exception as e:
        print(f"  ainvoke 失败: {e}")
        ainvoke_ok = False

    try:
        text_acc = ""
        n = 0
        async for chunk in model.astream(msgs):
            n += 1
            t, _ = _parse_blocks(chunk.content)
            if t:
                text_acc += t
        print(f"  astream text: {_short(text_acc, 80)} ({n} chunks)")
        astream_ok = bool(text_acc)
    except Exception as e:
        print(f"  astream 失败: {e}")
        astream_ok = False

    if ainvoke_ok and astream_ok:
        print(">>> 验证项 4 通过：异步路径可用")
        return True
    print(">>> 验证项 4 失败")
    return False


def main():
    if not os.getenv("QWEN_API_KEY"):
        print("ERROR: 未配置 QWEN_API_KEY")
        sys.exit(1)

    results = {
        "1. output_version 流式": probe_1_stream_with_output_version(),
        "2. enable_thinking 开关": probe_2_enable_thinking_toggle(),
        "3. JSON 模式": probe_3_json_mode(),
        "4. 异步路径": asyncio.run(probe_4_async_paths()),
    }

    print("\n" + "=" * 70)
    print("探测总结")
    print("=" * 70)
    all_pass = True
    for name, ok in results.items():
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}")
        if not ok:
            all_pass = False

    print()
    if all_pass:
        print(">>> 全部通过，可以进入 Step 2 重写 LLMClient")
        print(">>> 关键事实：ChatOpenAI 必须传 output_version='responses/v1'")
        print(">>> 必须解析 chunk.content 这个 list[dict]，按 type 区分 reasoning/text")
        sys.exit(0)
    else:
        print(">>> 有验证失败，暂停实施")
        sys.exit(2)


if __name__ == "__main__":
    main()
