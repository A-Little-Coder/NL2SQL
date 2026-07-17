/**
 * 对话区（任务 8.1, 8.3, 8.4, 8.5，决策 D5）
 *
 * 职责：
 * - 从 store.turns 渲染：每个 Turn 显示用户消息气泡（userQuery）+ 助手 Turn 卡片
 * - 助手卡片内组合：AgentTimeline（常驻时间轴）/ ClarificationBubble（反问气泡）/ ResultTable（结果表）
 * - 缓存命中短路（8.3）：AgentTimeline 内部已仅渲染 cache 节点，Conversation 层仍展示结果
 * - rejection/error（8.4）：展示 turn.error 理由，不渲染 ResultTable
 * - 底部输入框（Input.TextArea + 发送按钮，Shift+Enter 换行，Enter 发送）：
 *   - 发送时调 useQueryStream().sendQuery({ query, sessionId, userId, dbId })
 *   - currentSessionId 为空时先 createSession 拿到 session_id 再发送（场景 6.5）
 *   - selectedDbId 为空时提示"请先选择数据库"
 *   - sending 且最新轮 streaming 时，发送按钮变体为"停止"（abort + cancelTurn，change clarify-choice-inspector-cancel）
 * - 冷库加载提示（任务 15.8）：store.loadingDb 为 true 时在 streaming Turn 下方显示文案
 * - streaming 中的 Turn 显示加载指示
 * - 自动滚动到底部（新 turn 或新事件时）
 *
 * store 依赖：turns / currentSessionId / userId / selectedDbId / loadingDb / setCurrentSessionId
 * hook 依赖：useQueryStream().sendQuery
 */
import { useEffect, useRef, useState } from 'react';
import {
  Input,
  Button,
  Card,
  Spin,
  Alert,
  Avatar,
  Typography,
  Space,
  message,
} from 'antd';
import {
  SendOutlined,
  StopOutlined,
  UserOutlined,
  RobotOutlined,
  LoadingOutlined,
  DatabaseOutlined,
} from '@ant-design/icons';
import type { Turn } from '@/store/types';
import { useChatStore } from '@/store/useChatStore';
import { useQueryStream } from '@/hooks/useQueryStream';
import { createSession } from '@/api/rest';
import AgentTimeline from '../AgentTimeline';
import ClarificationBubble from '../ClarificationBubble';
import ResultTable from '../ResultTable';

const { Text, Paragraph } = Typography;

/** 用户消息气泡 */
function UserBubble({ query }: { query: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
      <Space align="start" size={8}>
        <div
          style={{
            background: '#1677ff',
            color: '#fff',
            padding: '8px 14px',
            borderRadius: 12,
            borderTopRightRadius: 2,
            maxWidth: 520,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {query}
        </div>
        <Avatar size="small" icon={<UserOutlined />} style={{ background: '#1677ff', flexShrink: 0 }} />
      </Space>
    </div>
  );
}

/** 助手 Turn 卡片 */
function AssistantCard({ turn, loadingDb }: { turn: Turn; loadingDb: boolean }) {
  const isStreaming = turn.status === 'streaming';
  const isAwaiting = turn.status === 'awaiting_clarification';
  const isError = turn.status === 'error';
  const isCancelled = turn.status === 'cancelled';
  // rejection 或 error 状态均不渲染结果表（8.4）；cancelled 也不渲染（change clarify-choice-inspector-cancel）
  const showResult = !!turn.result && !isError && !turn.rejection && !isCancelled;
  // 冷库加载提示仅在当前流式 Turn 下显示
  const showLoadingDbHint = loadingDb && isStreaming;

  return (
    <div style={{ display: 'flex', marginBottom: 16 }}>
      <Avatar size="small" icon={<RobotOutlined />} style={{ background: '#52c41a', flexShrink: 0, marginRight: 8 }} />
      <Card
        size="small"
        style={{ flex: 1, maxWidth: 640, background: '#fff' }}
        styles={{ body: { padding: 12 } }}
      >
        {/* 常驻时间轴 */}
        <AgentTimeline turn={turn} />

        {/* streaming 加载指示（multi-session-concurrency：排队态显示"排队中…"） */}
        {isStreaming && (
          <div style={{ marginTop: 8, color: '#1677ff', fontSize: 13 }}>
            <LoadingOutlined /> {turn.queued ? '排队中…' : '推理进行中…'}
          </div>
        )}

        {/* 冷库加载提示（任务 15.8 文案） */}
        {showLoadingDbHint && (
          <Alert
            type="info"
            showIcon
            icon={<DatabaseOutlined />}
            message="首次加载该数据库，约需数秒…"
            style={{ marginTop: 8 }}
          />
        )}

        {/* 反问气泡（awaiting_clarification 时显示，组件内部已判断） */}
        <ClarificationBubble turn={turn} />

        {/* 拒答/错误理由（8.4） */}
        {isError && turn.error && (
          <Alert
            type={turn.rejection ? 'warning' : 'error'}
            showIcon
            message={turn.rejection ? '无法回答该问题' : '处理出错'}
            description={turn.error}
            style={{ marginTop: 8 }}
          />
        )}

        {/* 已取消提示（change clarify-choice-inspector-cancel） */}
        {isCancelled && (
          <Alert
            type="info"
            showIcon
            message="已取消"
            description="用户已取消该请求"
            style={{ marginTop: 8 }}
          />
        )}

        {/* 反问期间不渲染结果表（spec: 反问期间不展示最终结果） */}
        {showResult && !isAwaiting && (
          <div style={{ marginTop: 12 }}>
            <ResultTable turn={turn} />
          </div>
        )}
      </Card>
    </div>
  );
}

export default function Conversation() {
  const turns = useChatStore((s) => s.turns);
  const currentSessionId = useChatStore((s) => s.currentSessionId);
  const userId = useChatStore((s) => s.userId);
  const selectedDbId = useChatStore((s) => s.selectedDbId);
  const loadingDb = useChatStore((s) => s.loadingDb);
  const setCurrentSessionId = useChatStore((s) => s.setCurrentSessionId);

  const { sendQuery, cancel } = useQueryStream();

  const [input, setInput] = useState('');
  // multi-session-concurrency：当前会话在途态由 store.turns 派生（不再用全局 sending），
  // 跨会话不互斥；同会话单在途由 isStreaming 拦截发送
  const isStreaming = turns.some((t) => t.status === 'streaming');
  const streamingTurnId = turns.find((t) => t.status === 'streaming')?.turnId ?? null;
  // 防止同一 tick 内重复点击发送（startTurn 同步注册后即由 isStreaming 拦截）
  const submittingRef = useRef(false);

  // 自动滚动到底部（新 turn 或新事件时）
  const bottomRef = useRef<HTMLDivElement>(null);
  const turnsLen = turns.length;
  const lastTimelineLen = turnsLen > 0 ? turns[turnsLen - 1].timeline.length : 0;
  const lastTurnStatus = turnsLen > 0 ? turns[turnsLen - 1].status : '';
  // 停止按钮：当前会话有在途 Turn 时显示（单会话单在途，覆盖初始请求与反问续流）
  const canStop = isStreaming;
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turnsLen, lastTimelineLen, lastTurnStatus]);

  /** 发送查询：处理 session_id 为空时自动创建（场景 6.5）；单会话单在途 */
  const handleSend = async () => {
    const query = input.trim();
    if (!query) return;
    if (!selectedDbId) {
      message.warning('请先选择数据库');
      return;
    }
    if (isStreaming || submittingRef.current) return; // 单会话单在途

    submittingRef.current = true;
    setInput('');
    try {
      let sessionId = currentSessionId;
      // session_id 为空：先创建会话（场景 6.5：未知 session_id 自动创建）
      if (!sessionId) {
        const res = await createSession({ user_id: userId });
        sessionId = res.session_id;
        setCurrentSessionId(sessionId);
      }
      await sendQuery({
        query,
        sessionId,
        userId,
        dbId: selectedDbId,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : '发送失败';
      message.error(`发送失败：${msg}`);
    } finally {
      submittingRef.current = false;
    }
  };

  /** 停止当前会话在途请求：abort fetch + cancelTurn（不影响其他会话在途流） */
  const handleStop = () => {
    if (streamingTurnId) cancel(streamingTurnId);
  };

  /** 输入框按键：Enter 发送（在途时不触发），Shift+Enter 换行 */
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!isStreaming) void handleSend();
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      {/* 消息列表区 */}
      <div style={{ flex: 1, overflow: 'auto', padding: '0 4px' }}>
        {turns.length === 0 ? (
          <div style={{ textAlign: 'center', color: '#999', padding: '48px 0' }}>
            <RobotOutlined style={{ fontSize: 32, marginBottom: 12 }} />
            <Paragraph type="secondary">
              输入你的问题，例如：查询近 7 天销售额 Top10 的商品
            </Paragraph>
          </div>
        ) : (
          turns.map((turn) => (
            <div key={turn.turnId}>
              <UserBubble query={turn.userQuery} />
              <AssistantCard turn={turn} loadingDb={loadingDb} />
            </div>
          ))
        )}
        {/* 滚动锚点 */}
        <div ref={bottomRef} />
      </div>

      {/* 底部输入区 */}
      <div style={{ flexShrink: 0, padding: '12px 4px 0', borderTop: '1px solid #f0f0f0', background: '#fff' }}>
        <Space.Compact style={{ width: '100%' }}>
          <Input.TextArea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={selectedDbId ? '输入问题，Enter 发送，Shift+Enter 换行' : '请先在顶部选择数据库'}
            autoSize={{ minRows: 1, maxRows: 5 }}
            style={{ borderRadius: '6px 0 0 6px', resize: 'none' }}
          />
          {canStop ? (
            <Button
              danger
              icon={<StopOutlined />}
              onClick={handleStop}
              style={{ height: 'auto', borderRadius: '0 6px 6px 0' }}
            >
              停止
            </Button>
          ) : (
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={() => void handleSend()}
              disabled={!input.trim()}
              style={{ height: 'auto', borderRadius: '0 6px 6px 0' }}
            >
              发送
            </Button>
          )}
        </Space.Compact>
        {!selectedDbId && (
          <Text type="warning" style={{ fontSize: 12, display: 'block', marginTop: 4 }}>
            请先选择数据库后再发起查询
          </Text>
        )}
        {isStreaming && (
          <div style={{ marginTop: 4 }}>
            <Spin size="small" />
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
              {loadingDb ? '首次加载该数据库，约需数秒…' : '处理中…'}
            </Text>
          </div>
        )}
      </div>
    </div>
  );
}
