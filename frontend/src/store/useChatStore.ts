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
  /** 设置检查器选中节点（null=自动跟随最新，D5） */
  selectNode: (turnId: string, node: TimelineNodeType | null) => void;
  /** 从会话历史 SessionTurn[] 构造 Turn[]（简化：仅 userQuery/finalSql/result） */
  setHistoryTurns: (turns: SessionTurn[]) => void;
}

export const useChatStore = create<ChatState>((set) => ({
  userId: 'default',
  viewMode: 'chat',
  dbList: [],
  selectedDbId: null,
  sessions: [],
  currentSessionId: null,
  turns: [],
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
      turns: state.turns.map((t) =>
        t.turnId === turnId ? { ...t, selectedNode: node } : t,
      ),
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
}));
