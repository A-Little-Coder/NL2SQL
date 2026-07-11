/**
 * 会话侧栏（任务 6.1-6.5）
 *
 * 职责：
 * - 顶部"新会话"按钮：调 createSession({user_id})，成功后 setCurrentSessionId 并刷新列表
 * - 渲染会话列表（store.sessions，后端已按 updated_at 降序返回，前端直接渲染）：
 *   每项显示 session_id（截断）、turn_count、status、updated_at；高亮 currentSessionId
 * - userId 变化时重新 listSessions(userId).then(res => setSessions(res.sessions ?? []))
 * - 点击会话项：getSessionHistory(sessionId, userId)，返回非 ErrorResponse（无 error 字段）
 *   则 setHistoryTurns(turns) + setCurrentSessionId(sessionId)；ErrorResponse 则 message 提示
 * - 删除会话：每项删除按钮 + Popconfirm 确认 -> deleteSession(sessionId, userId)，
 *   成功后从 sessions 移除；若删的是 currentSessionId 则切首个会话或清空
 * - 空态用 Empty 提示"暂无会话"
 * - 加载态用 Spin
 *
 * store 依赖：sessions / currentSessionId / userId / setSessions / setCurrentSessionId / setHistoryTurns
 * api 依赖：listSessions / createSession / getSessionHistory / deleteSession
 */
import { useEffect, useState, useCallback } from 'react';
import {
  Button,
  Empty,
  List,
  message,
  Popconfirm,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { createSession, deleteSession, getSessionHistory, listSessions } from '@/api/rest';
import type { ErrorResponse, SessionSummary } from '@/api/types';
import { useChatStore } from '@/store/useChatStore';

const { Text } = Typography;

/** 会话状态 -> Tag 颜色映射，便于一眼区分活跃/结束/异常 */
function statusColor(status: string): string {
  const s = status.toLowerCase();
  if (s === 'active' || s === 'ok') return 'green';
  if (s === 'error' || s === 'failed') return 'red';
  if (s === 'archived' || s === 'closed') return 'default';
  return 'blue';
}

/** session_id 通常较长（uuid），截断显示前 8 位 + 省略号，完整值放 Tooltip */
function shortSessionId(id: string): string {
  if (!id) return '';
  return id.length > 10 ? `${id.slice(0, 8)}…` : id;
}

/** ISO 时间字符串格式化为简短的"月-日 时:分" */
function formatTime(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${mm}-${dd} ${hh}:${mi}`;
}

/** 判断返回值是否为 ErrorResponse（后端部分接口 200 + error body） */
function isErrorResponse(obj: unknown): obj is ErrorResponse {
  return (
    typeof obj === 'object' &&
    obj !== null &&
    'error' in obj &&
    typeof (obj as Record<string, unknown>).error === 'string'
  );
}

export default function SessionSidebar() {
  // ---- 从 store 读取所需切片（selector 避免整体重渲染）----
  const sessions = useChatStore((s) => s.sessions);
  const currentSessionId = useChatStore((s) => s.currentSessionId);
  const userId = useChatStore((s) => s.userId);
  const setSessions = useChatStore((s) => s.setSessions);
  const setCurrentSessionId = useChatStore((s) => s.setCurrentSessionId);
  const setHistoryTurns = useChatStore((s) => s.setHistoryTurns);

  // ---- 本地加载态 ----
  const [loadingList, setLoadingList] = useState(false);
  const [creating, setCreating] = useState(false);
  const [loadingHistoryId, setLoadingHistoryId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  /** 拉取会话列表（user_id 变化或手动刷新时调用） */
  const refreshSessions = useCallback(
    async (uid: string) => {
      setLoadingList(true);
      try {
        const res = await listSessions(uid);
        setSessions(res.sessions ?? []);
      } catch (err) {
        const msg = err instanceof Error ? err.message : '加载会话列表失败';
        message.error(msg);
        setSessions([]);
      } finally {
        setLoadingList(false);
      }
    },
    [setSessions],
  );

  // userId 变化时重新拉取会话列表
  useEffect(() => {
    refreshSessions(userId);
  }, [userId, refreshSessions]);

  /** 新建会话：createSession({user_id}) -> setCurrentSessionId + 刷新列表 */
  const handleCreate = useCallback(async () => {
    setCreating(true);
    try {
      const res = await createSession({ user_id: userId });
      setCurrentSessionId(res.session_id);
      // 新建后清空当前对话区轮次（避免显示上一会话历史）
      setHistoryTurns([]);
      await refreshSessions(userId);
      message.success('已创建新会话');
    } catch (err) {
      const msg = err instanceof Error ? err.message : '创建会话失败';
      message.error(msg);
    } finally {
      setCreating(false);
    }
  }, [userId, setCurrentSessionId, setHistoryTurns, refreshSessions]);

  /** 点击会话项：加载历史轮次到对话区 + 切换 currentSessionId */
  const handleSelect = useCallback(
    async (sessionId: string) => {
      // 点击当前已选中会话不重复加载
      if (sessionId === currentSessionId) return;
      setLoadingHistoryId(sessionId);
      try {
        const res = await getSessionHistory(sessionId, userId);
        if (isErrorResponse(res)) {
          message.error(res.error || '加载历史失败');
          return;
        }
        setHistoryTurns(res.turns ?? []);
        setCurrentSessionId(sessionId);
      } catch (err) {
        const msg = err instanceof Error ? err.message : '加载历史失败';
        message.error(msg);
      } finally {
        setLoadingHistoryId(null);
      }
    },
    [userId, currentSessionId, setHistoryTurns, setCurrentSessionId],
  );

  /** 删除会话：成功后从 sessions 移除；若删的是当前会话则切首个或清空 */
  const handleDelete = useCallback(
    async (sessionId: string) => {
      setDeletingId(sessionId);
      try {
        const res = await deleteSession(sessionId, userId);
        if (isErrorResponse(res)) {
          message.error(res.error || '删除会话失败');
          return;
        }
        // 从本地列表移除（无需重新拉全量，减少闪烁）
        const remaining = sessions.filter((s) => s.session_id !== sessionId);
        setSessions(remaining);
        // 若删的是当前会话：切首个或清空
        if (sessionId === currentSessionId) {
          if (remaining.length > 0) {
            const next = remaining[0];
            // 主动加载首个会话历史
            setLoadingHistoryId(next.session_id);
            try {
              const hist = await getSessionHistory(next.session_id, userId);
              if (!isErrorResponse(hist)) {
                setHistoryTurns(hist.turns ?? []);
                setCurrentSessionId(next.session_id);
              } else {
                setCurrentSessionId(null);
                setHistoryTurns([]);
              }
            } catch {
              setCurrentSessionId(null);
              setHistoryTurns([]);
            } finally {
              setLoadingHistoryId(null);
            }
          } else {
            setCurrentSessionId(null);
            setHistoryTurns([]);
          }
        }
        message.success('已删除会话');
      } catch (err) {
        const msg = err instanceof Error ? err.message : '删除会话失败';
        message.error(msg);
      } finally {
        setDeletingId(null);
      }
    },
    [userId, sessions, currentSessionId, setSessions, setCurrentSessionId, setHistoryTurns],
  );

  // ---- 渲染 ----
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: 8 }}>
      {/* 顶部操作区：新会话 + 刷新 */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          loading={creating}
          onClick={handleCreate}
          block
        >
          新会话
        </Button>
        <Tooltip title="刷新会话列表">
          <Button
            icon={<ReloadOutlined />}
            onClick={() => refreshSessions(userId)}
            loading={loadingList}
          />
        </Tooltip>
      </div>

      {/* 列表区：加载态 / 空态 / 列表 */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {loadingList && sessions.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 24 }}>
            <Spin tip="加载会话列表…" />
          </div>
        ) : sessions.length === 0 ? (
          <Empty description="暂无会话" style={{ marginTop: 32 }} />
        ) : (
          <List
            dataSource={sessions}
            renderItem={(item: SessionSummary) => {
              const isActive = item.session_id === currentSessionId;
              const isLoadingHistory = loadingHistoryId === item.session_id;
              const isDeleting = deletingId === item.session_id;
              return (
                <List.Item
                  style={{
                    padding: '8px 10px',
                    cursor: 'pointer',
                    background: isActive ? '#e6f4ff' : undefined,
                    borderLeft: isActive ? '3px solid #1677ff' : '3px solid transparent',
                    borderRadius: 4,
                    marginBottom: 4,
                    transition: 'background 0.2s',
                  }}
                  onClick={() => handleSelect(item.session_id)}
                  actions={[
                    <Popconfirm
                      key="delete"
                      title="确认删除该会话？"
                      description="删除后不可恢复"
                      okText="删除"
                      cancelText="取消"
                      okButtonProps={{ danger: true }}
                      onConfirm={(e) => {
                        e?.stopPropagation();
                        handleDelete(item.session_id);
                      }}
                      onCancel={(e) => e?.stopPropagation()}
                    >
                      <Button
                        type="text"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        loading={isDeleting}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </Popconfirm>,
                  ]}
                >
                  <div style={{ width: '100%', minWidth: 0 }}>
                    {/* 第一行：session_id 截断 + status Tag */}
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                        marginBottom: 2,
                      }}
                    >
                      <Tooltip title={item.session_id}>
                        <Text strong={isActive} style={{ fontSize: 13 }}>
                          {shortSessionId(item.session_id)}
                        </Text>
                      </Tooltip>
                      <Tag color={statusColor(item.status)} style={{ margin: 0, fontSize: 11 }}>
                        {item.status}
                      </Tag>
                    </div>
                    {/* 第二行：轮次数 + 更新时间 */}
                    <div style={{ fontSize: 11, color: '#888' }}>
                      <span>{item.turn_count} 轮</span>
                      <span style={{ margin: '0 6px' }}>·</span>
                      <span>{formatTime(item.updated_at)}</span>
                    </div>
                    {/* 加载历史时的行内 Spin */}
                    {isLoadingHistory && (
                      <div style={{ marginTop: 4 }}>
                        <Spin size="small" />
                      </div>
                    )}
                  </div>
                </List.Item>
              );
            }}
          />
        )}
      </div>
    </div>
  );
}
