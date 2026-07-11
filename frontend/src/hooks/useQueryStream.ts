/**
 * SSE 订阅 Hook（决策 D4）
 *
 * 封装两个动作：
 * - sendQuery：发送初始查询，生成新 turnId，事件 reduce 进该 Turn
 * - sendResume：携带 resume=<回答> 续流，事件并入同一 turnId（D4）
 *
 * 网络错误降级为合成 error 事件并入 Turn；AbortController 支持取消。
 */
import { useCallback, useRef } from 'react';
import type { QueryRequest, SseEvent } from '@/api/types';
import { streamQuery } from '@/api/sse';
import { genTurnId, useChatStore } from '@/store/useChatStore';

interface SendQueryParams {
  query: string;
  sessionId: string;
  userId: string;
  dbId: string;
}

interface SendResumeParams {
  answer: string;
  sessionId: string;
  userId: string;
  dbId: string;
  /** 续流的同一 turnId（D4） */
  turnId: string;
}

export function useQueryStream() {
  const abortRef = useRef<AbortController | null>(null);

  /** 发送初始查询，返回新生成的 turnId */
  const sendQuery = useCallback(async ({ query, sessionId, userId, dbId }: SendQueryParams): Promise<string> => {
    const turnId = genTurnId();
    const store = useChatStore.getState();
    store.startTurn(turnId, query);

    const body: QueryRequest = {
      query,
      session_id: sessionId,
      user_id: userId,
      db_id: dbId,
    };

    const controller = new AbortController();
    abortRef.current = controller;
    store.setLoadingDb(true);

    try {
      await streamQuery(body, {
        signal: controller.signal,
        onEvent: (e: SseEvent) => useChatStore.getState().applyEvent(turnId, e),
      });
    } catch (err) {
      // 用户主动取消不视为错误
      if (!controller.signal.aborted) {
        useChatStore.getState().applyEvent(turnId, toErrorEvent(err));
      }
    } finally {
      useChatStore.getState().setLoadingDb(false);
    }
    return turnId;
  }, []);

  /** 发送 resume 续流，事件并入同一 turnId（D4） */
  const sendResume = useCallback(async ({ answer, sessionId, userId, dbId, turnId }: SendResumeParams): Promise<void> => {
    useChatStore.getState().resumeTurn(turnId);

    const body: QueryRequest = {
      query: '',
      session_id: sessionId,
      user_id: userId,
      db_id: dbId,
      resume: answer,
    };

    const controller = new AbortController();
    abortRef.current = controller;
    useChatStore.getState().setLoadingDb(false);

    try {
      await streamQuery(body, {
        signal: controller.signal,
        onEvent: (e: SseEvent) => useChatStore.getState().applyEvent(turnId, e),
      });
    } catch (err) {
      if (!controller.signal.aborted) {
        useChatStore.getState().applyEvent(turnId, toErrorEvent(err));
      }
    }
  }, []);

  /** 取消当前流 */
  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  return { sendQuery, sendResume, cancel };
}

/** 把网络错误降级为合成 error 事件（query_id 留空，reducer 容错） */
function toErrorEvent(err: unknown): SseEvent {
  const msg =
    err instanceof Error ? err.message : typeof err === 'string' ? err : '网络错误';
  return {
    type: 'error',
    data: { query_id: '', error: msg },
  };
}
