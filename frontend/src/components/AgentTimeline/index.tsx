/**
 * Agent 时间轴（任务 8.2-8.4，决策 D5）
 *
 * 职责：
 * - 在助手 Turn 卡片内渲染常驻时间轴，节点按 status 点亮颜色
 * - 每节点一行摘要（缓存命中/关键词组/候选数/可回答性/决策/结果等）
 * - 点击节点 -> store.selectNode(turnId, node.type)（D5 pin 到检查器）；
 *   若该节点已是 selectedNode 则再点取消（设 null），支持解除锁定
 * - 当前 selectedNode（turn.selectedNode）高亮
 * - 缓存命中（turn.details.cache?.hit）时仅渲染 cache 节点（8.3 短路）
 *
 * props: { turn: Turn }
 * store 依赖：selectNode（通过 useChatStore.getState() 直读，避免重渲染）
 */
import { Timeline, Popover, Button } from 'antd';
import {
  DatabaseOutlined,
  SearchOutlined,
  PartitionOutlined,
  QuestionCircleOutlined,
  CodeOutlined,
  PlayCircleOutlined,
  CheckCircleOutlined,
  CommentOutlined,
  TableOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  LockOutlined,
  ClockCircleOutlined,
  SafetyOutlined,
  AuditOutlined,
  EditOutlined,
  ToolOutlined,
  SolutionOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import type { ReactNode } from 'react';
import type { Turn, TimelineNode, TimelineNodeType, NodeStatus } from '@/store/types';
import { useChatStore } from '@/store/useChatStore';

/** 节点类型 -> 中文标签 */
const NODE_LABEL: Record<TimelineNodeType, string> = {
  pre_reject: '前置检查',
  rewrite_detect: '改写检测',
  rewrite: '改写',
  cache: '缓存检测',
  value_rewrite: '值改写',
  cache_confirm: '确认复用',
  ir: '信息检索',
  ss: 'Schema 选择',
  schema_empty: '未匹配表',
  permission: '权限检查',
  answerability: '可回答性',
  cg: 'SQL 生成',
  execution: '执行',
  decision: '最终决策',
  clarify: '反问',
  result: '结果',
  error: '错误',
};

/** 节点类型 -> 图标 */
const NODE_ICON: Record<TimelineNodeType, ReactNode> = {
  pre_reject: <SafetyOutlined />,
  rewrite_detect: <AuditOutlined />,
  rewrite: <EditOutlined />,
  cache: <DatabaseOutlined />,
  value_rewrite: <ToolOutlined />,
  cache_confirm: <SolutionOutlined />,
  ir: <SearchOutlined />,
  ss: <PartitionOutlined />,
  schema_empty: <WarningOutlined />,
  permission: <LockOutlined />,
  answerability: <QuestionCircleOutlined />,
  cg: <CodeOutlined />,
  execution: <PlayCircleOutlined />,
  decision: <CheckCircleOutlined />,
  clarify: <CommentOutlined />,
  result: <TableOutlined />,
  error: <CloseCircleOutlined />,
};

/** 节点状态 -> AntD Timeline 颜色 */
function statusColor(status: NodeStatus): string {
  switch (status) {
    case 'pending':
      return 'gray';
    case 'active':
      return 'blue';
    case 'done':
      return 'green';
    case 'error':
      return 'red';
    case 'cancelled':
      return 'gray';
    default:
      return 'gray';
  }
}

/** 节点状态 -> 状态图标（active 时叠加 loading） */
function nodeDot(node: TimelineNode): ReactNode {
  const icon = NODE_ICON[node.type];
  if (node.status === 'active') {
    return <LoadingOutlined style={{ color: '#1677ff' }} />;
  }
  // error 状态用红色图标
  if (node.status === 'error') {
    return <span style={{ color: '#ff4d4f' }}>{icon}</span>;
  }
  // cancelled 状态用灰色图标（停止旋转，表示该节点因取消未完成）
  if (node.status === 'cancelled') {
    return <span style={{ color: '#999' }}>{icon}</span>;
  }
  return icon;
}

export default function AgentTimeline({ turn }: { turn: Turn }) {
  const selectedNode = turn.selectedNode;
  const cacheHit = turn.details.cache?.hit === true;
  // 用户在 cache_confirm 选择「重新生成」(approved=false) -> 否定复用，后端走完整 ir/ss/cg 链路；
  // 此时不应短路隐藏这些节点（change clarify-choice-inspector-cancel）
  const cacheRejected = turn.details.cacheConfirm?.approved === false;
  // 本轮是否被检查器锁定（change clarify-choice-inspector-cancel）
  const isPinned = useChatStore((s) => s.inspectorTurnId) === turn.turnId;

  // 缓存命中短路（8.3）：保留前置检查/改写/值改写/确认等辅助节点 + cache + result + error，
  // 跳过 ir/ss/answerability/cg/execution/decision（cache 命中且用户确认复用时这些被短路）
  let nodes = turn.timeline;
  if (cacheHit && !cacheRejected) {
    nodes = turn.timeline.filter(
      (n) =>
        n.type === 'pre_reject' ||
        n.type === 'rewrite_detect' ||
        n.type === 'rewrite' ||
        n.type === 'cache' ||
        n.type === 'value_rewrite' ||
        n.type === 'cache_confirm' ||
        n.type === 'result' ||
        n.type === 'error',
    );
    // 若 reducer 还没追加 result/error，至少保留 cache 节点
    if (nodes.length === 0) {
      nodes = turn.timeline.filter((n) => n.type === 'cache');
    }
  }

  // 空时间轴：流尚未推送任何节点时显示等待提示
  if (nodes.length === 0) {
    return (
      <div style={{ color: '#999', fontSize: 13, padding: '8px 0' }}>
        <ClockCircleOutlined /> 等待推理事件…
      </div>
    );
  }

  /** 点击节点：pin 到检查器；若已是选中节点则取消（设 null） */
  const handleClick = (node: TimelineNode) => {
    const current = turn.selectedNode;
    if (current === node.type) {
      // 再点取消锁定，恢复自动跟随
      useChatStore.getState().selectNode(turn.turnId, null);
    } else {
      useChatStore.getState().selectNode(turn.turnId, node.type);
    }
  };

  const items = nodes.map((node) => {
    const isSelected = selectedNode === node.type;
    const color = statusColor(node.status);
    const label = NODE_LABEL[node.type];
    return {
      key: node.id ?? node.type,
      color,
      dot: nodeDot(node),
      children: (
        <div
          onClick={() => handleClick(node)}
          style={{
            cursor: 'pointer',
            padding: '2px 6px',
            margin: '-2px -6px',
            borderRadius: 4,
            background: isSelected ? 'rgba(22, 119, 255, 0.08)' : 'transparent',
            borderLeft: isSelected ? '3px solid #1677ff' : '3px solid transparent',
            transition: 'background 0.2s',
          }}
          title={isSelected ? '点击取消锁定（恢复自动跟随）' : '点击锁定到检查器'}
        >
          <span style={{ fontWeight: isSelected ? 600 : 500, marginRight: 6 }}>
            {label}
          </span>
          <span
            style={{ color: '#666', fontSize: 13 }}
            title={node.type === 'cache' && turn.details.cache ? `source=${turn.details.cache.source}` : undefined}
          >
            {node.summary || (node.status === 'active' ? '进行中…' : '')}
          </span>
          {node.type === 'cache' &&
            turn.details.cache?.source === 'metric_definition' &&
            turn.details.cache?.matchedMetricName && (
              <Popover
                title="长期记忆指标"
                content={
                  <div style={{ maxWidth: 360 }}>
                    <div style={{ marginBottom: 4 }}>
                      <b>指标名：</b>
                      {turn.details.cache.matchedMetricName}
                    </div>
                    <div>
                      <b>复用 SQL：</b>
                      <pre style={{ margin: '4px 0 0', whiteSpace: 'pre-wrap', fontSize: 12, background: '#f5f5f5', padding: 8, borderRadius: 4 }}>
                        {turn.details.cache.cachedSql ?? ''}
                      </pre>
                    </div>
                  </div>
                }
                trigger="click"
              >
                <Button type="link" size="small" style={{ padding: '0 4px', height: 'auto' }} onClick={(e) => e.stopPropagation()}>
                  查看
                </Button>
              </Popover>
            )}
        </div>
      ),
    };
  });

  return (
    <div>
      {isPinned && (
        <div style={{ fontSize: 12, color: '#722ed1', marginBottom: 2 }}>
          <LockOutlined /> 检查器正查看此轮
        </div>
      )}
      <Timeline
        items={items}
        style={{ marginTop: 8, marginBottom: 0 }}
      />
    </div>
  );
}
