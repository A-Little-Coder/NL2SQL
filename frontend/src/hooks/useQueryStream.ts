/**
 * SSE 订阅 Hook（决策 D4 + multi-session-concurrency）
 *
 * 封装两个动作：
 * - sendQuery：发送初始查询，生成新 turnId，事件 reduce 进该 Turn
 * - sendResume：携带 resume=<回答> 续流，事件并入同一 turnId（D4）
 *
 * multi-session-concurrency 改造：
 * - abortController 按 turnId 存于 Map（支持多在途流并行），cancel(turnId) 精确 abort
 * - 流消费 fire-and-forget（不阻塞调用方）-> 跨会话并发不互斥
 * - SSE 事件按 sessionId 路由：当前会话进 turns，后台会话进 turnsBySession（持续更新）
 *
 * 网络错误降级为合成 error 事件并入 Turn；cancel(turnId) abort fetch 并 cancelTurn 置
 * cancelled 终态（修复僵尸 streaming，change clarify-choice-inspector-cancel）。
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
  // multi-session-concurrency：按 turnId 跟踪各自在途流的 AbortController（多在途并行）
  const abortMap = useRef<Map<string, AbortController>>(new Map());

  /** 后台消费一条 SSE 流：事件按 sessionId 路由 reduce，结束清理 abortMap（不阻塞调用方） */
  const consumeStream = useCallback(
    async (
      turnId: string,
      sessionId: string,
      body: QueryRequest,
      controller: AbortController,
    ) => {
      const routeEvent = (e: SseEvent) => {
        useChatStore.getState().applyEventToSession(sessionId, turnId, e);
      };
      try {
        await streamQuery(body, { signal: controller.signal, onEvent: routeEvent });
      } catch (err) {
        // 用户主动取消不视为错误
        if (!controller.signal.aborted) {
          routeEvent(toErrorEvent(err));
        }
      } finally {
        abortMap.current.delete(turnId);
        useChatStore.getState().setLoadingDb(false);
      }
    },
    [],
  );

  /** 发送初始查询，返回新生成的 turnId（流在后台消费，不阻塞） */
  const sendQuery = useCallback(
    async ({ query, sessionId, userId, dbId }: SendQueryParams): Promise<string> => {
      const turnId = genTurnId();
      const store = useChatStore.getState();
      store.startTurn(turnId, query);

      const controller = new AbortController();
      abortMap.current.set(turnId, controller);
      store.setLoadingDb(true);

      const body: QueryRequest = {
        query,
        session_id: sessionId,
        user_id: userId,
        db_id: dbId,
      };
      // 后台消费流（不阻塞调用方 -> 跨会话并发不互斥）
      void consumeStream(turnId, sessionId, body, controller);
      return turnId;
    },
    [consumeStream],
  );

  /** 发送 resume 续流，事件并入同一 turnId（D4；流在后台消费） */
  const sendResume = useCallback(
    async ({ answer, sessionId, userId, dbId, turnId }: SendResumeParams): Promise<void> => {
      useChatStore.getState().resumeTurn(turnId);

      const controller = new AbortController();
      abortMap.current.set(turnId, controller);

      const body: QueryRequest = {
        query: '',
        session_id: sessionId,
        user_id: userId,
        db_id: dbId,
        resume: answer,
      };
      void consumeStream(turnId, sessionId, body, controller);
    },
    [consumeStream],
  );

  /** 取消指定在途流：abort fetch + cancelTurn 置终态（不影响其他在途流） */
  const cancel = useCallback((turnId: string) => {
    const c = abortMap.current.get(turnId);
    if (c) {
      c.abort();
      abortMap.current.delete(turnId);
    }
    useChatStore.getState().cancelTurn(turnId);
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
