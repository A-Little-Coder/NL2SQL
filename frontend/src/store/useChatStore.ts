/**
 * 全局状态 store（Zustand，决策 D1/D3）
 *
 * 持有：sessions / currentSessionId / dbList / selectedDbId / userMemory /
 * userMetrics / turns / userId / viewMode。Turn 的 SSE 事件通过 applyEvent
 * 调用 reduceSseEvent 累积进对应 turnId（D4：turnId 客户端生成，跨 resume 稳定）。
 */
import { create } from 'zustand';
import type {
  DatabaseInfo,
  MetricDefinitionResponse,
  SessionEventsTurn,
  SessionSummary,
  SessionTurn,
  SseEvent,
  UserMemoryResponse,
} from '@/api/types';
import type { TimelineNodeType, Turn } from './types';
import { createTurn, reduceSseEvent } from './reducer';

export type ViewMode = 'chat' | 'memory';

/**
 * 客户端生成 turnId（D4）。
 * 优先用 crypto.randomUUID，回退到时间戳+随机串（兼容旧环境/测试）。
 */
export function genTurnId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
  } catch {
    /* ignore */
  }
  return `turn-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

interface ChatState {
  // ---- 全局 ----
  userId: string;
  viewMode: ViewMode;
  // ---- 数据库 ----
  dbList: DatabaseInfo[];
  selectedDbId: string | null;
  // ---- 会话 ----
  sessions: SessionSummary[];
  currentSessionId: string | null;
  // ---- 轮次（当前会话）----
  turns: Turn[];
  /** 按会话缓存的 turns（D7，同次会话内切回用完整行兜底） */
  turnsBySession: Record<string, Turn[]>;
  /** 检查器锁定的 turnId（change clarify-choice-inspector-cancel）；null=自动跟随最新 turn */
  inspectorTurnId: string | null;
  // ---- 用户记忆 ----
  userMemory: UserMemoryResponse | null;
  userMetrics: MetricDefinitionResponse | null;
  // ---- 冷库加载提示 ----
  loadingDb: boolean;

  // ---- setters ----
  setUserId: (id: string) => void;
  setViewMode: (m: ViewMode) => void;
  setDbList: (list: DatabaseInfo[]) => void;
  setSelectedDbId: (id: string | null) => void;
  setSessions: (s: SessionSummary[]) => void;
  setCurrentSessionId: (id: string | null) => void;
  setTurns: (t: Turn[]) => void;
  setUserMemory: (m: UserMemoryResponse | null) => void;
  setUserMetrics: (m: MetricDefinitionResponse | null) => void;
  setLoadingDb: (b: boolean) => void;

  // ---- turn 操作 ----
  /** 用传入 turnId 创建新 Turn 并追加（新查询，selectedNode 自动为 null） */
  startTurn: (turnId: string, userQuery: string) => void;
  /** 把 SSE 事件 reduce 进指定 turnId 的 Turn */
  applyEvent: (turnId: string, event: SseEvent) => void;
  /** resume 前把 Turn 状态恢复为 streaming（保留已有 timeline） */
  resumeTurn: (turnId: string) => void;
  /** 设置检查器选中节点（null=自动跟随最新，D5）；同时 pin 检查器到该 turn（change clarify-choice-inspector-cancel） */
  selectNode: (turnId: string, node: TimelineNodeType | null) => void;
  /** 解除检查器 turn 锁定，恢复自动跟随最新（change clarify-choice-inspector-cancel） */
  releaseInspector: () => void;
  /** 用户取消在途请求：Turn 进 cancelled 终态，时间轴追加"用户已取消"节点（change clarify-choice-inspector-cancel） */
  cancelTurn: (turnId: string) => void;
  /** 从会话历史 SessionTurn[] 构造 Turn[]（简化：仅 userQuery/finalSql/result；老会话摘要回落） */
  setHistoryTurns: (turns: SessionTurn[]) => void;
  /** 从事件流重放还原 Turn[]（D2，新会话；reducer 零改动） */
  setTurnsFromEvents: (turns: SessionEventsTurn[]) => void;
  /** 缓存当前会话的 turns（D7，切换离开前调用） */
  cacheCurrentTurns: (sessionId: string) => void;
  /** 从缓存加载会话 turns，返回是否命中（D7，切回时优先调用） */
  loadCachedTurns: (sessionId: string) => boolean;
}

export const useChatStore = create<ChatState>((set, get) => ({
  userId: 'default',
  viewMode: 'chat',
  dbList: [],
  selectedDbId: null,
  sessions: [],
  currentSessionId: null,
  turns: [],
  turnsBySession: {},
  inspectorTurnId: null,
  userMemory: null,
  userMetrics: null,
  loadingDb: false,

  setUserId: (id) => set({ userId: id }),
  setViewMode: (m) => set({ viewMode: m }),
  setDbList: (list) => set({ dbList: list }),
  setSelectedDbId: (id) => set({ selectedDbId: id }),
  setSessions: (s) => set({ sessions: s }),
  setCurrentSessionId: (id) => set({ currentSessionId: id }),
  setTurns: (t) => set({ turns: t }),
  setUserMemory: (m) => set({ userMemory: m }),
  setUserMetrics: (m) => set({ userMetrics: m }),
  setLoadingDb: (b) => set({ loadingDb: b }),

  startTurn: (turnId, userQuery) =>
    set((state) => ({
      // 新查询：新增 Turn（selectedNode=null 即自动跟随），保留历史轮次
      turns: [...state.turns, createTurn(turnId, userQuery)],
    })),

  applyEvent: (turnId, event) =>
    set((state) => ({
      turns: state.turns.map((t) =>
        t.turnId === turnId ? reduceSseEvent(t, event) : t,
      ),
    })),

  resumeTurn: (turnId) =>
    set((state) => ({
      turns: state.turns.map((t) =>
        t.turnId === turnId ? { ...t, status: 'streaming' as const } : t,
      ),
    })),

  selectNode: (turnId, node) =>
    set((state) => ({
      // node===null（点已选中节点解除锁定）-> inspectorTurnId 也置 null，恢复全自动跟随
      // node!=null -> pin 检查器到该 turn（change clarify-choice-inspector-cancel）
      inspectorTurnId: node === null ? null : turnId,
      turns: state.turns.map((t) =>
        t.turnId === turnId ? { ...t, selectedNode: node } : t,
      ),
    })),

  releaseInspector: () => set({ inspectorTurnId: null }),

  cancelTurn: (turnId) =>
    set((state) => ({
      turns: state.turns.map((t) => {
        if (t.turnId !== turnId) return t;
        const cancelNode = { type: 'error' as const, status: 'done' as const, summary: '用户已取消' };
        // 把所有 active 节点置为 cancelled（灰色停止旋转），避免取消后节点一直转
        let timeline = t.timeline.map((n) =>
          n.status === 'active' ? { ...n, status: 'cancelled' as const } : n,
        );
        const idx = timeline.findIndex((n) => n.type === 'error');
        timeline = idx >= 0
          ? timeline.map((n, i) => (i === idx ? { ...n, ...cancelNode } : n))
          : [...timeline, cancelNode];
        return {
          ...t,
          status: 'cancelled' as const,
          cancelled: true,
          rejection: false,
          error: '用户已取消请求',
          timeline,
        };
      }),
    })),

  setHistoryTurns: (historyTurns) =>
    set({
      turns: historyTurns.map((ht, idx): Turn => {
        const turnId = `history-${idx}`;
        const sql = ht.final_sql ?? '';
        const resultMeta = ht.result_meta;
        const turn = createTurn(turnId, ht.user_query ?? '');
        turn.status = 'done';
        turn.queryId = undefined;
        if (sql) {
          turn.result = {
            sql,
            rows: resultMeta ? Array(resultMeta.row_count).fill({}) : [],
          };
          turn.timeline = [
            ...turn.timeline,
            {
              type: 'result',
              status: 'done',
              summary: `历史结果 · ${resultMeta?.row_count ?? 0} 行`,
            },
          ];
        }
        if (ht.rejection_reason) {
          turn.rejection = true;
          turn.error = ht.rejection_reason;
          turn.status = 'error';
          turn.timeline = [
            ...turn.timeline,
            {
              type: 'error',
              status: 'done',
              summary: `拒答: ${ht.rejection_reason}`,
            },
          ];
        }
        return turn;
      }),
    }),

  setTurnsFromEvents: (eventsTurns) => {
    const turns: Turn[] = eventsTurns.map((et): Turn => {
      const turn = createTurn(`history-${et.turn_index}`, et.user_query ?? '');
      const reduced = et.events.reduce((t, evt) => reduceSseEvent(t, evt), turn);
      // D4: 检测 result 事件存储侧截断标记（__truncated__），供 ResultTable 显示"历史快照·前20行"提示
      const resultTruncated = et.events.some(
        (e) => e.type === 'result' && (e.data as Record<string, unknown> | undefined)?.__truncated__,
      );
      return { ...reduced, resultTruncated };
    });
    set({ turns, inspectorTurnId: null });
  },

  cacheCurrentTurns: (sessionId) =>
    set((state) => ({
      turnsBySession: { ...state.turnsBySession, [sessionId]: state.turns },
    })),

  loadCachedTurns: (sessionId) => {
    const cached = get().turnsBySession[sessionId];
    if (cached) {
      set({ turns: cached, inspectorTurnId: null });
      return true;
    }
    return false;
  },
}));
