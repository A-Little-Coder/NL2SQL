/**
 * SSE 流式客户端（决策 D2）
 *
 * /query 是 POST + JSON body，原生 EventSource 不支持（仅 GET），故用
 * fetch + ReadableStream 自写行解析器：
 *   - 按 `\n\n` 切事件块
 *   - `data:` 前缀取 JSON payload（{ type, data }）
 *   - `:` 前缀为注释行（`: heartbeat` 心跳），不产生可见事件
 *   - AbortController 支持取消
 */
import type { QueryRequest, SseEvent } from './types';

const BASE = '/api/v1';

export interface StreamQueryOptions {
  /** 每解析出一个 SSE 事件回调一次 */
  onEvent: (event: SseEvent) => void;
  /** 支持取消 */
  signal?: AbortSignal;
}

/**
 * 发起 POST /query SSE 流，逐事件回调 onEvent。
 *
 * 非 2xx 或无 body 时抛 Error（调用方降级为 error 事件并入 Turn）。
 */
export async function streamQuery(
  body: QueryRequest,
  { onEvent, signal }: StreamQueryOptions,
): Promise<void> {
  const res = await fetch(`${BASE}/query`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '');
    let msg = `HTTP ${res.status}`;
    if (text) {
      try {
        const j = JSON.parse(text);
        msg = j.detail || j.error || msg;
      } catch {
        msg = text;
      }
    }
    throw new Error(msg);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  try {
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // 按 \n\n 切事件块（一个块可能跨多次 read）
      let sep: number;
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const chunk = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        parseSseChunk(chunk, onEvent);
      }
    }
    // flush 流末尾残余
    if (buffer.trim()) {
      parseSseChunk(buffer, onEvent);
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* ignore */
    }
  }
}

/**
 * 解析单个 SSE 事件块（由 \n\n 分隔的文本）。
 *
 * - `:` 开头为注释/心跳行 -> 跳过，不产生事件
 * - `data:` 开头 -> 累积为事件 payload（多行 data 用 \n 拼接）
 * - payload JSON 形如 { type, data } -> 回调 onEvent
 */
export function parseSseChunk(
  chunk: string,
  onEvent: (event: SseEvent) => void,
): void {
  const lines = chunk.split('\n');
  const dataLines: string[] = [];
  for (const rawLine of lines) {
    const line = rawLine.replace(/\r$/, '');
    if (line === '') continue;
    if (line.startsWith(':')) {
      // 注释/心跳行，不产生事件
      continue;
    }
    if (line.startsWith('data:')) {
      // data: 后可有一个可选空格
      const val = line.slice(5);
      dataLines.push(val.startsWith(' ') ? val.slice(1) : val);
    }
    // 其它字段（event:/id:/retry:）当前不使用
  }
  if (dataLines.length === 0) return;
  const payloadStr = dataLines.join('\n');
  try {
    const parsed = JSON.parse(payloadStr) as { type?: string; data?: unknown };
    if (parsed && typeof parsed.type === 'string') {
      onEvent(parsed as SseEvent);
    }
  } catch {
    // 非 JSON payload，忽略（不破坏流）
  }
}
