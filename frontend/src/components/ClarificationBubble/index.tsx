/**
 * 反问内联气泡（任务 11.1-11.4，决策 D6）
 *
 * 职责：
 * - 仅当 turn.status==='awaiting_clarification' && turn.clarification 时渲染；否则 return null
 * - 内联气泡（AntD Alert，不用 Modal）展示：
 *   - turn.clarification.question（反问问题）
 *   - turn.clarification.kind 决定渲染（change clarify-choice-inspector-cancel）：
 *     confirm=二选一按钮(无输入框) / choice=按钮组+输入框 / open=纯输入框(回退兼容 ambiguities)
 *   - 选项点击即提交对应 value；输入框提交原文
 *   - 显示当前 round（"第 N 轮反问"）
 * - 用户作答 -> 调 useQueryStream().sendResume({ answer, sessionId, userId, dbId, turnId })
 * - 提交后显示 loading（resume 流进行中）；支持多轮（round 递增，每次按当前 turn.clarification 渲染）
 * - 反问期间不渲染结果表（本组件只管气泡，结果表由 Conversation 控制）
 *
 * props: { turn: Turn }
 * hook 依赖：useQueryStream().sendResume
 * store 依赖：currentSessionId / userId / selectedDbId
 */
import { useState } from 'react';
import { Alert, Button, Input, Space, Spin, Tag, Typography } from 'antd';
import { SendOutlined } from '@ant-design/icons';
import type { Turn } from '@/store/types';
import { useQueryStream } from '@/hooks/useQueryStream';
import { useChatStore } from '@/store/useChatStore';

const { Text } = Typography;

export default function ClarificationBubble({ turn }: { turn: Turn }) {
  // 顶层调用 hook（规范要求）
  const { sendResume } = useQueryStream();
  // store 取值（currentSessionId / userId / selectedDbId）
  const currentSessionId = useChatStore((s) => s.currentSessionId);
  const userId = useChatStore((s) => s.userId);
  const selectedDbId = useChatStore((s) => s.selectedDbId);

  const [answer, setAnswer] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // 非反问态不渲染
  if (turn.status !== 'awaiting_clarification' || !turn.clarification) {
    return null;
  }

  const { question, ambiguities, round, kind, options } = turn.clarification;
  // 结构化反问类型（change clarify-choice-inspector-cancel）：缺失按 'open' 兜底
  const effectiveKind = kind ?? 'open';
  const hasOptions = !!options && options.length > 0;

  /**
   * 提交回答触发 resume 续流
   * 事件并入同一 turnId（D4），时间轴在反问节点后继续追加
   */
  const doResume = async (resumeAnswer: string) => {
    const trimmed = resumeAnswer.trim();
    if (!trimmed) return;
    // 缺少必要参数时不提交（防御性，理论上不会发生）
    if (!currentSessionId || !selectedDbId) return;

    setSubmitting(true);
    try {
      await sendResume({
        answer: trimmed,
        sessionId: currentSessionId,
        userId,
        dbId: selectedDbId,
        turnId: turn.turnId,
      });
    } finally {
      setSubmitting(false);
      setAnswer('');
    }
  };

  // resume 进行中展示 loading 态
  if (submitting) {
    return (
      <div style={{ margin: '8px 0', padding: '12px 16px' }}>
        <Spin tip="正在提交回答，resume 流进行中…" size="small">
          <div style={{ minHeight: 40 }} />
        </Spin>
      </div>
    );
  }

  return (
    <Alert
      type="info"
      showIcon
      style={{ margin: '8px 0' }}
      message={
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          {/* 第 N 轮反问标记 */}
          <Text type="secondary" style={{ fontSize: 12 }}>
            第 {round} 轮反问
          </Text>
          {/* 反问问题 */}
          <Text strong>{question}</Text>

          {/* confirm 类型：二选一主按钮，无输入框（change clarify-choice-inspector-cancel） */}
          {effectiveKind === 'confirm' && hasOptions ? (
            <Space style={{ marginTop: 4 }}>
              {options!.map((opt) => (
                <Button
                  key={opt.value}
                  type={opt.value === 'yes' ? 'primary' : 'default'}
                  onClick={() => doResume(opt.value)}
                  disabled={submitting}
                >
                  {opt.label}
                </Button>
              ))}
            </Space>
          ) : (
            <>
              {/* choice/open：选项按钮（优先 options，回退 ambiguities 兼容旧后端） */}
              {hasOptions ? (
                <div style={{ marginTop: 4 }}>
                  <Text type="secondary" style={{ fontSize: 12, marginRight: 8 }}>
                    可选答案：
                  </Text>
                  {options!.map((opt) => (
                    <Tag
                      key={opt.value}
                      style={{ cursor: 'pointer', marginBottom: 4 }}
                      color="blue"
                      onClick={() => doResume(opt.value)}
                    >
                      {opt.label}
                    </Tag>
                  ))}
                </div>
              ) : ambiguities && ambiguities.length > 0 ? (
                <div>
                  <Text type="secondary" style={{ fontSize: 12, marginRight: 8 }}>
                    可选答案：
                  </Text>
                  {ambiguities.map((opt) => (
                    <Tag
                      key={opt}
                      style={{ cursor: 'pointer', marginBottom: 4 }}
                      color="blue"
                      onClick={() => doResume(opt)}
                    >
                      {opt}
                    </Tag>
                  ))}
                </div>
              ) : null}
              {/* 自定义回答输入框（choice/open 有；confirm 已在上分支渲染无输入框） */}
              <Space.Compact style={{ width: '100%', marginTop: 4 }}>
                <Input
                  placeholder="输入你的回答…"
                  value={answer}
                  onChange={(e) => setAnswer(e.target.value)}
                  onPressEnter={() => doResume(answer)}
                  disabled={submitting}
                />
                <Button
                  type="primary"
                  icon={<SendOutlined />}
                  onClick={() => doResume(answer)}
                  disabled={!answer.trim() || submitting}
                >
                  提交
                </Button>
              </Space.Compact>
            </>
          )}
        </Space>
      }
    />
  );
}
