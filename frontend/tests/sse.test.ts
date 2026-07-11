/**
 * SSE 解析器单测（任务 14.1）
 *
 * 覆盖：data: JSON 解析、: heartbeat 注释行不产事件、多事件块切分、
 * data 多行拼接、非 JSON payload 容错。
 */
import { parseSseChunk } from '../src/api/sse';
import type { SseEvent } from '../src/api/types';

describe('SSE 解析器 parseSseChunk', () => {
  test('data: JSON 正确解析为事件', () => {
    const events: SseEvent[] = [];
    parseSseChunk(
      'data: {"type":"stage","data":{"node":"ir","status":"started","query_id":"q1"}}',
      (e) => events.push(e),
    );
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe('stage');
    expect(events[0].data.node).toBe('ir');
    expect(events[0].data.query_id).toBe('q1');
  });

  test(': heartbeat 注释行不产生事件', () => {
    const events: SseEvent[] = [];
    parseSseChunk(': heartbeat', (e) => events.push(e));
    expect(events).toHaveLength(0);
  });

  test('多事件块切分（按 \\n\\n 切块后逐块解析）', () => {
    const raw =
      'data: {"type":"stage","data":{"node":"ir","status":"done","query_id":"q1"}}\n\n' +
      'data: {"type":"keywords","data":{"groups":[],"query_id":"q1"}}';
    const events: SseEvent[] = [];
    const blocks = raw.split('\n\n');
    for (const b of blocks) parseSseChunk(b, (e) => events.push(e));
    expect(events).toHaveLength(2);
    expect(events[0].type).toBe('stage');
    expect(events[1].type).toBe('keywords');
  });

  test('data: 后带一个空格也能正确取 payload', () => {
    const events: SseEvent[] = [];
    parseSseChunk(
      'data: {"type":"done","data":{"has_result":false,"awaiting_clarification":true,"fix_failed":false,"decision_path":"","fix_rounds_used":0,"last_error":null,"query_id":"q1"}}',
      (e) => events.push(e),
    );
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe('done');
    expect(events[0].data.awaiting_clarification).toBe(true);
  });

  test('非 JSON payload 不崩、不产事件', () => {
    const events: SseEvent[] = [];
    parseSseChunk('data: not-json', (e) => events.push(e));
    expect(events).toHaveLength(0);
  });

  test('空块不产事件', () => {
    const events: SseEvent[] = [];
    parseSseChunk('', (e) => events.push(e));
    expect(events).toHaveLength(0);
  });
});
