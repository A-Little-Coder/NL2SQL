/**
 * REST 接口封装（决策 D9）
 *
 * 所有调用走同源 `/api/v1/...`，开发期由 Vite proxy 转发到 FastAPI :8000。
 * 镜像后端路由：databases.py / session.py / user.py / app.py(health)。
 */
import type {
  CreateSessionRequest,
  CreateSessionResponse,
  DatabaseListResponse,
  ErrorResponse,
  HealthResponse,
  MetricDefinitionResponse,
  SessionHistoryResponse,
  SessionListResponse,
  TableListResponse,
  UserMemoryResponse,
} from './types';

const BASE = '/api/v1';

/** 带 status 的错误，调用方可据 status 做差异化处理（如 404 回退） */
export interface ApiError extends ErrorResponse {
  status: number;
}

/**
 * 统一 fetch 封装：JSON 进 JSON 出，非 2xx 抛 ApiError。
 *
 * 注意：后端部分接口在资源不存在时返回 200 + ErrorResponse body（如
 * getSessionHistory / deleteSession），调用方需自行判断返回值是否含 `error` 字段。
 */
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  const text = await res.text();
  let data: unknown = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { error: text };
    }
  }
  if (!res.ok) {
    const errObj = (data && typeof data === 'object' ? data : {}) as Partial<ErrorResponse>;
    const err: ApiError = {
      error: errObj.error || `HTTP ${res.status}`,
      detail: errObj.detail,
      status: res.status,
    };
    throw err;
  }
  return data as T;
}

/** GET /api/v1/health */
export const getHealth = () => request<HealthResponse>(`${BASE}/health`);

/** GET /api/v1/databases —— 列出所有可用数据库 */
export const listDatabases = () =>
  request<DatabaseListResponse>(`${BASE}/databases`);

/** GET /api/v1/databases/{db_id}/tables —— 列出指定库的表清单 */
export const listTables = (dbId: string) =>
  request<TableListResponse>(`${BASE}/databases/${encodeURIComponent(dbId)}/tables`);

/** POST /api/v1/sessions —— 显式创建会话 */
export const createSession = (body: CreateSessionRequest) =>
  request<CreateSessionResponse>(`${BASE}/sessions`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

/** GET /api/v1/sessions?user_id=xxx —— 列出用户会话（updated_at 降序） */
export const listSessions = (userId: string) =>
  request<SessionListResponse>(
    `${BASE}/sessions?user_id=${encodeURIComponent(userId)}`,
  );

/** GET /api/v1/sessions/{id}/history?user_id=xxx —— 获取会话历史轮次 */
export const getSessionHistory = (sessionId: string, userId: string) =>
  request<SessionHistoryResponse | ErrorResponse>(
    `${BASE}/sessions/${encodeURIComponent(sessionId)}/history?user_id=${encodeURIComponent(userId)}`,
  );

/** DELETE /api/v1/sessions/{id}?user_id=xxx —— 删除会话 */
export const deleteSession = (sessionId: string, userId: string) =>
  request<{ status: string; session_id: string } | ErrorResponse>(
    `${BASE}/sessions/${encodeURIComponent(sessionId)}?user_id=${encodeURIComponent(userId)}`,
    { method: 'DELETE' },
  );

/** GET /api/v1/users/{id}/memory —— 用户长期记忆 */
export const getUserMemory = (userId: string) =>
  request<UserMemoryResponse>(`${BASE}/users/${encodeURIComponent(userId)}/memory`);

/** GET /api/v1/users/{id}/metrics —— 指标定义列表 */
export const getUserMetrics = (userId: string) =>
  request<MetricDefinitionResponse>(`${BASE}/users/${encodeURIComponent(userId)}/metrics`);
