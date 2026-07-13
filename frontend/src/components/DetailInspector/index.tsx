/**
 * 节点详情检查器（任务 9.1-9.3 + 10.1-10.2，决策 D5）
 *
 * 职责：
 * - 从 store.turns 取"当前 turn"：inspectorTurnId 锁定时看该 turn，否则最后一个 turn
 *   （change clarify-choice-inspector-cancel，跨轮可回看）；turns 为空显示空态
 * - 确定显示哪个节点详情（D5）：
 *   · turn.selectedNode === null  -> 自动跟随最新节点（timeline 最后一个节点的 type）
 *   · turn.selectedNode !== null  -> 锁定显示该 type 的节点详情
 * - 顶部显示当前 turn 的 userQuery、status 标签、selectedNode 状态
 * - 按节点类型渲染详情（9.2），从 turn.details 取数据（字段名严格按 store/types.ts）
 * - 思考链区域（10.1）：turn.thinking 按节点展示，每节点一个 Collapse 面板，
 *   内容累积思考文本，展开时打字机式自动滚动到底部；
 *   整体思考链区域用外层 Collapse 默认折叠
 * - 10.2：turn.thinking 为空对象时完全不渲染思考链区域（非思考模型降级）
 *
 * store 依赖：turns（取当前 turn，selectedNode 判定自动跟随/锁定）
 */
import { useEffect, useRef } from 'react';
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Empty,
  Tag,
  Typography,
} from 'antd';
import type { CollapseProps } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  LockOutlined,
  ThunderboltOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { useChatStore } from '@/store/useChatStore';
import type {
  ExecutionResult,
  TimelineNodeType,
  Turn,
} from '@/store/types';

const { Text, Paragraph, Title } = Typography;

// ---------------------------------------------------------------------------
// 节点类型 -> 中文标签映射（与时间轴展示保持一致）
// ---------------------------------------------------------------------------
const NODE_LABEL: Record<TimelineNodeType, string> = {
  pre_reject: '前置检查',
  rewrite_detect: '改写检测',
  rewrite: '改写执行',
  cache: '历史缓存',
  value_rewrite: '值改写',
  cache_confirm: '确认复用',
  ir: '信息检索',
  ss: 'Schema 选择',
  schema_empty: '未匹配表',
  answerability: '可回答性检查',
  cg: '候选生成',
  execution: 'SQL 执行',
  decision: '最终决策',
  clarify: '反问',
  result: '最终结果',
  error: '错误/拒答',
};

// Turn 状态 -> Tag 颜色与文案
const STATUS_TAG: Record<
  Turn['status'],
  { color: string; text: string }
> = {
  streaming: { color: 'processing', text: '推理中' },
  done: { color: 'success', text: '已完成' },
  error: { color: 'error', text: '错误' },
  awaiting_clarification: { color: 'warning', text: '等待反问回答' },
  cancelled: { color: 'default', text: '已取消' },
};

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------

/** 数值格式化为两位置信度（null/undefined 显示 '-'） */
function fmtConf(c: number | null | undefined): string {
  if (c == null || Number.isNaN(c)) return '-';
  return typeof c === 'number' ? c.toFixed(2) : String(c);
}

// ---------------------------------------------------------------------------
// 思考链面板（10.1）：展开时打字机式自动滚动到底部
// ---------------------------------------------------------------------------
function ThinkingPanel({ text }: { text: string }) {
  const ref = useRef<HTMLPreElement>(null);

  // 内容变化时滚动到底部（打字机式累积，保持末尾可见）
  useEffect(() => {
    const el = ref.current;
    if (el) {
 el.scrollTop = el.scrollHeight; }
  }, [text]);

  return (
    <pre
      ref={ref}
      style={{
        margin: 0,
        padding: '8px 12px',
        maxHeight: 320,
        overflow: 'auto',
        background: '#fafafa',
        border: '1px solid #f0f0f0',
        borderRadius: 4,
        fontSize: 12.5,
        lineHeight: 1.6,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        fontFamily:
          'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
      }}
    >
      {text || '（暂无思考内容）'}
    </pre>
  );
}

// ---------------------------------------------------------------------------
// 按节点类型渲染详情（9.2）
// ---------------------------------------------------------------------------

/** cache 节点：显示 hit/source/confidence/cachedSql */
function CacheDetail({ turn }: { turn: Turn }) {
  const c = turn.details.cache;
  if (!c) return <NoDetail />;
  return (
    <Descriptions column={1} size="small" bordered>
      <Descriptions.Item label="是否命中">
        {c.hit ? (
          <Tag icon={<CheckCircleOutlined />} color="success">
            命中
          </Tag>
        ) : (
          <Tag icon={<CloseCircleOutlined />} color="default">
            未命中
          </Tag>
        )}
      </Descriptions.Item>
      <Descriptions.Item label="来源">{c.source || '-'}</Descriptions.Item>
      <Descriptions.Item label="置信度">{fmtConf(c.confidence)}</Descriptions.Item>
      <Descriptions.Item label="缓存 SQL">
        {c.cachedSql ? (
          <Paragraph code copyable style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
            {c.cachedSql}
          </Paragraph>
        ) : (
          <Text type="secondary">无</Text>
        )}
      </Descriptions.Item>
    </Descriptions>
  );
}

/** ir 节点：按关键词组聚合展示关键词提取 + 字段召回 + 值召回（D5） */
function IrDetail({ turn }: { turn: Turn }) {
  const keywordGroups = turn.details.ir?.keywordGroups;
  if (!keywordGroups || keywordGroups.length === 0) return <NoDetail />;

  const panels = keywordGroups.map((g, i) => ({
    key: String(i),
    label: (
      <span>
        <Text strong>{g.phrase}</Text>
        <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
          {g.terms.length} 同义词 · {g.columns.length} 字段 · {g.values.length} 值
        </Text>
      </span>
    ),
    children: (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {g.terms.length > 0 && (
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>同义词：</Text>{' '}
            {g.terms.map((t, j) => (
              <Tag key={j} style={{ marginBottom: 2 }}>{t}</Tag>
            ))}
          </div>
        )}
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            召回字段 ({g.columns.length})：
          </Text>
          {g.columns.length > 0 ? (
            <div style={{ marginTop: 2 }}>
              {g.columns.map((c, j) => (
                <Tag key={j} color="blue" style={{ marginBottom: 2 }}>
                  {c.table}.{c.column}
                  <Text type="secondary" style={{ fontSize: 11, marginLeft: 4 }}>
                    {fmtConf(c.score)}
                  </Text>
                </Tag>
              ))}
            </div>
          ) : (
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>无召回</Text>
          )}
        </div>
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            召回值 ({g.values.length})：
          </Text>
          {g.values.length > 0 ? (
            <div style={{ marginTop: 2 }}>
              {g.values.map((v, j) => (
                <Tag key={j} color="purple" style={{ marginBottom: 2 }}>
                  {v.value}
                  <Text type="secondary" style={{ fontSize: 11, marginLeft: 4 }}>
                    {v.table}.{v.column} · {fmtConf(v.score)}
                  </Text>
                </Tag>
              ))}
            </div>
          ) : (
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>无召回</Text>
          )}
        </div>
      </div>
    ),
  }));

  return <Collapse size="small" defaultActiveKey={['0']} items={panels} />;
}

/** ss 节点：Schema 选择 + JOIN 路径注入详情（D4） */
function SsDetail({ turn }: { turn: Turn }) {
  const sf = turn.details.schemaFinalize;
  if (!sf) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="SS 阶段进行中或无 JOIN 路径数据"
        style={{ margin: '12px 0' }}
      />
    );
  }
  return (
    <Descriptions column={1} size="small" bordered>
      <Descriptions.Item label="JOIN 边数">{sf.joinEdges}</Descriptions.Item>
      <Descriptions.Item label="桥接表数">{sf.bridgeTables}</Descriptions.Item>
    </Descriptions>
  );
}

/** answerability 节点 */
function AnswerabilityDetail({ turn }: { turn: Turn }) {
  const a = turn.details.answerability;
  if (!a) return <NoDetail />;
  return (
    <Descriptions column={1} size="small" bordered>
      <Descriptions.Item label="是否可回答">
        {a.answerable ? (
          <Tag icon={<CheckCircleOutlined />} color="success">
            可回答
          </Tag>
        ) : (
          <Tag icon={<CloseCircleOutlined />} color="error">
            不可回答
          </Tag>
        )}
      </Descriptions.Item>
      <Descriptions.Item label="置信度">{fmtConf(a.confidence)}</Descriptions.Item>
      <Descriptions.Item label="理由">
        <Paragraph style={{ margin: 0 }}>{a.reason || '-'}</Paragraph>
      </Descriptions.Item>
    </Descriptions>
  );
}

/** cg 节点：候选 SQL 列表全文 */
function CgDetail({ turn }: { turn: Turn }) {
  const candidates = turn.details.candidates;
  if (!candidates || candidates.length === 0) return <NoDetail />;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {candidates.map((cand) => (
        <Card
          key={cand.id}
          size="small"
          title={<Text code>{cand.id}</Text>}
          styles={{ body: { padding: 8 } }}
        >
          <Paragraph
            code
            copyable
            style={{
              margin: 0,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontSize: 12.5,
            }}
          >
            {cand.sql}
          </Paragraph>
        </Card>
      ))}
    </div>
  );
}

/** execution 节点：逐候选显示 success/rows/error */
function ExecutionDetail({ turn }: { turn: Turn }) {
  const exec = turn.details.exec;
  if (!exec || Object.keys(exec).length === 0) return <NoDetail />;

  const entries = Object.entries(exec) as [string, ExecutionResult][];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {entries.map(([id, r]) => (
        <Card
          key={id}
          size="small"
          title={
            <span>
              <Text code>{r.candidateId ?? id}</Text>
              {r.success ? (
                <Tag
                  icon={<CheckCircleOutlined />}
                  color="success"
                  style={{ marginLeft: 8 }}
                >
                  成功
                </Tag>
              ) : (
                <Tag
                  icon={<CloseCircleOutlined />}
                  color="error"
                  style={{ marginLeft: 8 }}
                >
                  失败
                </Tag>
              )}
            </span>
          }
          styles={{ body: { padding: 8 } }}
        >
          <Descriptions column={1} size="small">
            <Descriptions.Item label="行数">
              {r.rows != null ? r.rows : '-'}
            </Descriptions.Item>
            {r.error && (
              <Descriptions.Item label="错误">
                <Text type="danger" style={{ fontSize: 12.5 }}>
                  {r.error}
                </Text>
              </Descriptions.Item>
            )}
          </Descriptions>
        </Card>
      ))}
    </div>
  );
}

/** decision 节点：selectedId/selectedSql/decisionPath/fixFailed/reason/multiIntent */
function DecisionDetail({ turn }: { turn: Turn }) {
  const d = turn.details.decision;
  if (!d) return <NoDetail />;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <Descriptions column={1} size="small" bordered>
        {d.multiIntent != null && (
          <Descriptions.Item label="多意图">
            {d.multiIntent ? (
              <Tag color="purple">多意图聚合</Tag>
            ) : (
              <Tag>单意图</Tag>
            )}
          </Descriptions.Item>
        )}
        {d.subqueryCount != null && (
          <Descriptions.Item label="子查询数">{d.subqueryCount}</Descriptions.Item>
        )}
        {d.successCount != null && (
          <Descriptions.Item label="成功数">{d.successCount}</Descriptions.Item>
        )}
        {d.selectedId != null && (
          <Descriptions.Item label="选中候选">
            <Text code>{d.selectedId || '-'}</Text>
          </Descriptions.Item>
        )}
        {d.fixFailed != null && (
          <Descriptions.Item label="修复失败">
            {d.fixFailed ? (
              <Tag color="error">是</Tag>
            ) : (
              <Tag color="default">否</Tag>
            )}
          </Descriptions.Item>
        )}
        {d.decisionPath && (
          <Descriptions.Item label="决策路径">
            <Text>{d.decisionPath}</Text>
          </Descriptions.Item>
        )}
        {d.reason && (
          <Descriptions.Item label="理由">
            <Paragraph style={{ margin: 0 }}>{d.reason}</Paragraph>
          </Descriptions.Item>
        )}
      </Descriptions>
      {d.selectedSql && (
        <Card size="small" title="选中 SQL" styles={{ body: { padding: 8 } }}>
          <Paragraph
            code
            copyable
            style={{
              margin: 0,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontSize: 12.5,
            }}
          >
            {d.selectedSql}
          </Paragraph>
        </Card>
      )}
    </div>
  );
}

/** clarify 节点：turn.clarification（question/ambiguities/round） */
function ClarifyDetail({ turn }: { turn: Turn }) {
  const c = turn.clarification;
  if (!c) return <NoDetail />;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label="反问轮次">第 {c.round} 轮</Descriptions.Item>
        <Descriptions.Item label="问题">
          <Paragraph style={{ margin: 0 }}>{c.question}</Paragraph>
        </Descriptions.Item>
      </Descriptions>
      {c.ambiguities && c.ambiguities.length > 0 && (
        <Card size="small" title="歧义点" styles={{ body: { padding: 8 } }}>
          {c.ambiguities.map((a, i) => (
            <Tag key={i} color="orange" style={{ marginBottom: 4 }}>
              {a}
            </Tag>
          ))}
        </Card>
      )}
    </div>
  );
}

/** result 节点：turn.result.sql + 行数 */
function ResultDetail({ turn }: { turn: Turn }) {
  const r = turn.result;
  if (!r) return <NoDetail />;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label="结果行数">{r.rows.length}</Descriptions.Item>
      </Descriptions>
      <Card size="small" title="最终 SQL" styles={{ body: { padding: 8 } }}>
        <Paragraph
          code
          copyable
          style={{
            margin: 0,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            fontSize: 12.5,
          }}
        >
          {r.sql}
        </Paragraph>
      </Card>
    </div>
  );
}

/** error 节点：turn.error（rejection 时标注"拒答"） */
function ErrorDetail({ turn }: { turn: Turn }) {
  if (!turn.error) return <NoDetail />;
  return (
    <Alert
      type={turn.cancelled ? 'info' : turn.rejection ? 'warning' : 'error'}
      showIcon
      message={turn.cancelled ? '已取消' : turn.rejection ? '拒答' : '错误'}
      description={turn.error}
    />
  );
}

/** 该节点 type 在 timeline 中但 details 无数据 */
function NoDetail() {
  return (
    <Empty
      image={Empty.PRESENTED_IMAGE_SIMPLE}
      description="该节点暂无结构化详情"
      style={{ margin: '12px 0' }}
    />
  );
}

/** 前置拒答 LLM 判定类别 -> 中文标签 */
const PRE_REJECT_CATEGORY_LABEL: Record<string, string> = {
  write_op: '写操作',
  dangerous_info: '危险信息',
  normal: '通过',
};

/** 前置拒答节点：通过/拒答原因 + LLM 判定类别（D9） */
function PreRejectDetail({ turn }: { turn: Turn }) {
  const p = turn.details.preReject;
  if (!p) return <NoDetail />;
  const categoryLabel = p.category ? (PRE_REJECT_CATEGORY_LABEL[p.category] ?? p.category) : null;
  return (
    <Descriptions column={1} size="small" bordered>
      <Descriptions.Item label="检测结果">
        {p.passed ? (
          <Tag icon={<CheckCircleOutlined />} color="success">通过</Tag>
        ) : (
          <Tag icon={<CloseCircleOutlined />} color="error">拒答</Tag>
        )}
      </Descriptions.Item>
      {categoryLabel && (
        <Descriptions.Item label="判定类别">
          <Tag color={p.category === 'dangerous_info' ? 'error' : (p.category === 'write_op' ? 'warning' : 'success')}>
            {categoryLabel}
          </Tag>
        </Descriptions.Item>
      )}
      {!p.passed && p.reason && (
        <Descriptions.Item label="拒答原因">{p.reason}</Descriptions.Item>
      )}
    </Descriptions>
  );
}

/** schema 空拒答节点：未选出表时显式拒答（D10） */
function SchemaEmptyDetail({ turn }: { turn: Turn }) {
  const s = turn.details.schemaEmpty;
  if (!s) return <NoDetail />;
  return (
    <Descriptions column={1} size="small" bordered>
      <Descriptions.Item label="检测结果">
        <Tag icon={<WarningOutlined />} color="error">未匹配表</Tag>
      </Descriptions.Item>
      <Descriptions.Item label="拒答原因">{s.reason}</Descriptions.Item>
    </Descriptions>
  );
}

/** 改写检测节点：按轮次展示检测到的问题 */
function RewriteDetectDetail({ turn }: { turn: Turn }) {
  const rounds = turn.details.rewriteDetect?.rounds;
  if (!rounds || rounds.length === 0) return <NoDetail />;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {rounds.map((r) => (
        <Card key={r.round} size="small" title={`第 ${r.round} 轮检测`}>
          <Descriptions column={1} size="small">
            <Descriptions.Item label="结果">
              {r.hasIssues ? (
                <Tag color="warning">发现问题</Tag>
              ) : (
                <Tag icon={<CheckCircleOutlined />} color="success">无问题</Tag>
              )}
            </Descriptions.Item>
            {r.hasIssues && (
              <>
                {r.issueTypes.length > 0 && (
                  <Descriptions.Item label="问题类型">
                    {r.issueTypes.map((t, i) => (
                      <Tag key={i} color="orange">{t}</Tag>
                    ))}
                  </Descriptions.Item>
                )}
                {r.issueDetail && (
                  <Descriptions.Item label="详情">{r.issueDetail}</Descriptions.Item>
                )}
              </>
            )}
          </Descriptions>
        </Card>
      ))}
    </div>
  );
}

/** 改写执行节点：按轮次展示原句->改写后/原因 */
function RewriteDetail({ turn }: { turn: Turn }) {
  const rounds = turn.details.rewrite?.rounds;
  if (!rounds || rounds.length === 0) return <NoDetail />;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {rounds.map((r) => (
        <Card key={r.round} size="small" title={`第 ${r.round} 轮改写`}>
          <Descriptions column={1} size="small">
            <Descriptions.Item label="原句">
              <Paragraph style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{r.originalQuery}</Paragraph>
            </Descriptions.Item>
            <Descriptions.Item label="改写后">
              <Paragraph code copyable style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{r.rewrittenQuery}</Paragraph>
            </Descriptions.Item>
            {r.reason && (
              <Descriptions.Item label="原因">{r.reason}</Descriptions.Item>
            )}
          </Descriptions>
        </Card>
      ))}
    </div>
  );
}

/** 值参数改写节点：历史查询/原缓存SQL->改写后SQL */
function ValueRewriteDetail({ turn }: { turn: Turn }) {
  const v = turn.details.valueRewrite;
  if (!v) return <NoDetail />;
  return (
    <Descriptions column={1} size="small" bordered>
      <Descriptions.Item label="是否改写">
        {v.changed ? (
          <Tag color="processing">已改写值参数</Tag>
        ) : (
          <Tag color="default">未变更</Tag>
        )}
      </Descriptions.Item>
      <Descriptions.Item label="历史查询">{v.historicalQuery || '-'}</Descriptions.Item>
      <Descriptions.Item label="当前查询">{v.userQuery || '-'}</Descriptions.Item>
      <Descriptions.Item label="原缓存 SQL">
        <Paragraph code copyable style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{v.cachedSql}</Paragraph>
      </Descriptions.Item>
      <Descriptions.Item label="改写后 SQL">
        <Paragraph code copyable style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{v.adjustedCachedSql}</Paragraph>
      </Descriptions.Item>
      {v.reason && (
        <Descriptions.Item label="原因">{v.reason}</Descriptions.Item>
      )}
    </Descriptions>
  );
}

/** 复用确认节点：用户确认结果 */
function CacheConfirmDetail({ turn }: { turn: Turn }) {
  const c = turn.details.cacheConfirm;
  if (!c) return <NoDetail />;
  return (
    <Descriptions column={1} size="small" bordered>
      <Descriptions.Item label="用户选择">
        {c.approved ? (
          <Tag icon={<CheckCircleOutlined />} color="success">确认复用</Tag>
        ) : (
          <Tag icon={<CloseCircleOutlined />} color="warning">重新生成</Tag>
        )}
      </Descriptions.Item>
      <Descriptions.Item label="回答内容">{c.userChoice || '-'}</Descriptions.Item>
      <Descriptions.Item label="历史查询">{c.historicalQuery || '-'}</Descriptions.Item>
      <Descriptions.Item label="当前查询">{c.userQuery || '-'}</Descriptions.Item>
    </Descriptions>
  );
}

/** 按节点类型分发渲染 */
function renderDetail(turn: Turn, type: TimelineNodeType) {
  switch (type) {
    case 'pre_reject':
      return <PreRejectDetail turn={turn} />;
    case 'rewrite_detect':
      return <RewriteDetectDetail turn={turn} />;
    case 'rewrite':
      return <RewriteDetail turn={turn} />;
    case 'cache':
      return <CacheDetail turn={turn} />;
    case 'value_rewrite':
      return <ValueRewriteDetail turn={turn} />;
    case 'cache_confirm':
      return <CacheConfirmDetail turn={turn} />;
    case 'ir':
      return <IrDetail turn={turn} />;
    case 'ss':
      return <SsDetail turn={turn} />;
    case 'schema_empty':
      return <SchemaEmptyDetail turn={turn} />;
    case 'answerability':
      return <AnswerabilityDetail turn={turn} />;
    case 'cg':
      return <CgDetail turn={turn} />;
    case 'execution':
      return <ExecutionDetail turn={turn} />;
    case 'decision':
      return <DecisionDetail turn={turn} />;
    case 'clarify':
      return <ClarifyDetail turn={turn} />;
    case 'result':
      return <ResultDetail turn={turn} />;
    case 'error':
      return <ErrorDetail turn={turn} />;
    default:
      return <NoDetail />;
  }
}

// ---------------------------------------------------------------------------
// 主组件
// ---------------------------------------------------------------------------
export default function DetailInspector() {
  const turns = useChatStore((s) => s.turns);
  const inspectorTurnId = useChatStore((s) => s.inspectorTurnId);
  const releaseInspector = useChatStore((s) => s.releaseInspector);
  // inspectorTurnId 锁定时看该 turn，否则自动跟随最新（change clarify-choice-inspector-cancel）
  const currentTurn: Turn | undefined = (() => {
    if (turns.length === 0) return undefined;
    if (inspectorTurnId) {
      const pinned = turns.find((t) => t.turnId === inspectorTurnId);
      if (pinned) return pinned;
    }
    return turns[turns.length - 1];
  })();
  // 是否锁定在非最新轮（用于显示"返回最新"按钮）
  const isPinnedToOld =
    !!currentTurn &&
    !!inspectorTurnId &&
    currentTurn.turnId !== turns[turns.length - 1].turnId;
  const pinnedTurnIndex = currentTurn
    ? turns.findIndex((t) => t.turnId === currentTurn.turnId) + 1
    : 0;

  // ---- 空态：无 turn ----
  if (!currentTurn) {
    return (
      <div style={{ padding: 16, height: '100%' }}>
        <Card size="small" style={{ marginBottom: 12 }}>
          <Title level={5} style={{ margin: 0 }}>
            节点详情检查器
          </Title>
        </Card>
        <Empty description="暂无活跃轮次" style={{ marginTop: 48 }} />
      </div>
    );
  }

  const turn = currentTurn;

  // ---- D5：确定显示哪个节点 ----
  // selectedNode === null -> 自动跟随最新节点（timeline 最后一个节点的 type）
  // selectedNode !== null -> 锁定显示该 type 的节点详情
  const isAuto = turn.selectedNode === null;
  let displayType: TimelineNodeType | null = null;

  if (!isAuto) {
    // 锁定模式：显示 selectedNode
    displayType = turn.selectedNode;
  } else if (turn.timeline.length > 0) {
    // 自动跟随：取 timeline 最后一个节点的 type
    displayType = turn.timeline[turn.timeline.length - 1].type;
  }

  // ---- 思考链（10.1 / 10.2）----
  // 10.2：turn.thinking 为空对象时完全不渲染思考链区域
  const thinkingEntries = Object.entries(turn.thinking);
  const hasThinking = thinkingEntries.length > 0;

  // 思考链内层面板
  const thinkingPanels: CollapseProps['items'] = thinkingEntries.map(
    ([node, text]) => ({
      key: node,
      label: (
        <span>
          <Text strong>{node}</Text>
          <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
            {text.length} 字
          </Text>
        </span>
      ),
      children: <ThinkingPanel text={text} />,
    }),
  );

  // ---- status 标签 ----
  const statusTag = STATUS_TAG[turn.status];

  return (
    <div style={{ padding: 12, height: '100%', overflow: 'auto' }}>
      {/* 顶部：标题 + 当前 turn 信息 */}
      <Card size="small" style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 8,
            }}
          >
            <Title level={5} style={{ margin: 0 }}>
              节点详情检查器
            </Title>
            <Tag color={statusTag.color}>{statusTag.text}</Tag>
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              用户问题
            </Text>
            <Paragraph
              style={{ margin: '4px 0 0 0', fontWeight: 500 }}
              ellipsis={{ rows: 3, tooltip: turn.userQuery }}
            >
              {turn.userQuery || '（空）'}
            </Paragraph>
          </div>
          <div>
            {isAuto ? (
              <Tag icon={<ThunderboltOutlined />} color="blue">
                自动跟随
              </Tag>
            ) : (
              <Tag icon={<LockOutlined />} color="gold">
                已锁定：{turn.selectedNode ? NODE_LABEL[turn.selectedNode] : '-'}
              </Tag>
            )}
          </div>
          {/* 锁定在非最新轮时显示"返回最新"（change clarify-choice-inspector-cancel） */}
          {isPinnedToOld && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <Tag icon={<LockOutlined />} color="purple">
                已锁定到第 {pinnedTurnIndex} 轮
              </Tag>
              <Button size="small" type="link" onClick={releaseInspector} style={{ padding: 0 }}>
                返回最新
              </Button>
            </div>
          )}
        </div>
      </Card>

      {/* 节点详情区（9.2）*/}
      <Card
        size="small"
        title={
          displayType ? (
            <span>
              节点详情 ·{' '}
              <Text strong>{NODE_LABEL[displayType]}</Text>
            </span>
          ) : (
            <span>节点详情</span>
          )
        }
        style={{ marginBottom: 12 }}
      >
        {displayType ? (
          renderDetail(turn, displayType)
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="暂无节点，等待推理开始…"
            style={{ margin: '12px 0' }}
          />
        )}
      </Card>

      {/* 思考链区域（10.1 / 10.2）*/}
      {hasThinking && (
        <Card size="small" title="qwen3 思考链" style={{ marginBottom: 12 }}>
          <Collapse
            size="small"
            items={thinkingPanels}
            // 默认折叠：不设置 defaultActiveKey
          />
        </Card>
      )}
    </div>
  );
}
