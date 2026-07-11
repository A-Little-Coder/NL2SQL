/**
 * 用户记忆视图（任务 13.1-13.3）
 *
 * 职责：
 * - 13.1：useEffect 依赖 store.userId，并行调用 getUserMemory + getUserMetrics，
 *   结果存入 store（setUserMemory/setUserMetrics）；加载中显示 Spin，失败显示 Alert
 * - 13.2：分区块展示 userMemory 的 6 个字段（term_preferences /
 *   frequently_used_tables / metric_definitions / query_preferences /
 *   domain_context / clarification_history），对象用 Descriptions、数组用 List、
 *   空值显示"暂无"；另用 Table 展示指标定义列表 userMetrics.metrics
 * - 13.3：userId 切换时自动重新请求并刷新（useEffect 依赖 userId）
 *
 * store 依赖：userId / userMemory / userMetrics / setUserMemory / setUserMetrics
 * api 依赖：getUserMemory / getUserMetrics
 */
import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Card,
  Descriptions,
  Empty,
  List,
  Spin,
  Table,
  Tag,
  Typography,
  type TableColumnsType,
} from 'antd';
import type {
  MetricDefinition,
  UserMemoryResponse,
} from '@/api/types';
import { getUserMemory, getUserMetrics } from '@/api/rest';
import { useChatStore } from '@/store/useChatStore';

const { Title, Paragraph, Text } = Typography;

/** 判断对象是否"为空"（无键或全部值为 null/undefined/空串/空对象/空数组） */
function isEmptyValue(v: unknown): boolean {
  if (v === null || v === undefined) return true;
  if (typeof v === 'string') return v.trim() === '';
  if (Array.isArray(v)) return v.length === 0;
  if (typeof v === 'object') return Object.keys(v as Record<string, unknown>).length === 0;
  return false;
}

/**
 * 把任意值渲染为可读的字符串。
 * - 对象/数组用 JSON.stringify（缩进 2），便于排查
 * - 基本类型直接 String()
 */
function renderScalar(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'object') return JSON.stringify(v, null, 2);
  return String(v);
}

/**
 * 渲染一个"对象区块"：Record<string, unknown>。
 * - 空 -> <Empty description="暂无" />
 * - 非空 -> AntD Descriptions，每个 key 一项，值用 Text/Paragraph 渲染
 *   （对象/数组值用 <pre> 展示 JSON）
 */
function ObjectBlock({ data }: { data: Record<string, unknown> }) {
  const keys = Object.keys(data);
  if (keys.length === 0) {
    return <Empty description="暂无" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  }
  return (
    <Descriptions column={1} size="small" bordered>
      {keys.map((k) => {
        const v = data[k];
        const isCompound =
          v !== null &&
          v !== undefined &&
          typeof v === 'object';
        return (
          <Descriptions.Item key={k} label={<Text strong>{k}</Text>}>
            {isEmptyValue(v) ? (
              <Text type="secondary">暂无</Text>
            ) : isCompound ? (
              <pre
                style={{
                  margin: 0,
                  padding: 8,
                  background: '#fafafa',
                  borderRadius: 4,
                  fontSize: 12,
                  maxHeight: 240,
                  overflow: 'auto',
                }}
              >
                {JSON.stringify(v, null, 2)}
              </pre>
            ) : (
              <Text>{renderScalar(v)}</Text>
            )}
          </Descriptions.Item>
        );
      })}
    </Descriptions>
  );
}

/**
 * 渲染一个"数组区块"：Record<string, unknown>[]。
 * - 空 -> <Empty description="暂无" />
 * - 非空 -> AntD List，每项展示其 JSON（紧凑模式，每项一行）
 */
function ArrayBlock({ data }: { data: Record<string, unknown>[] }) {
  if (!data || data.length === 0) {
    return <Empty description="暂无" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  }
  return (
    <List
      size="small"
      bordered
      dataSource={data}
      renderItem={(item, idx) => (
        <List.Item>
          <div style={{ width: '100%' }}>
            <Tag color="blue" style={{ marginRight: 8 }}>
              #{idx + 1}
            </Tag>
            <pre
              style={{
                margin: '4px 0 0 0',
                padding: 8,
                background: '#fafafa',
                borderRadius: 4,
                fontSize: 12,
                maxHeight: 200,
                overflow: 'auto',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {JSON.stringify(item, null, 2)}
            </pre>
          </div>
        </List.Item>
      )}
    />
  );
}

/** 区块标题统一样式 */
function SectionTitle({ children }: { children: React.ReactNode }) {
  return <Title level={5} style={{ marginTop: 0, marginBottom: 12 }}>{children}</Title>;
}

export default function UserMemoryView() {
  // ---- 从 store 读取所需切片（selector 避免整体重渲染）----
  const userId = useChatStore((s) => s.userId);
  const userMemory = useChatStore((s) => s.userMemory);
  const userMetrics = useChatStore((s) => s.userMetrics);
  const setUserMemory = useChatStore((s) => s.setUserMemory);
  const setUserMetrics = useChatStore((s) => s.setUserMetrics);

  // ---- 本地加载/错误态（store 只存数据，不存加载标志，故用本地 state）----
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 13.1 / 13.3：userId 变化时并行拉取记忆与指标，结果写回 store
  useEffect(() => {
    // 防御：userId 为空时不发请求
    if (!userId) {
      setUserMemory(null);
      setUserMetrics(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    // 并行请求两个接口；任一失败都展示错误，但互不阻塞写入
    Promise.allSettled([
      getUserMemory(userId),
      getUserMetrics(userId),
    ])
      .then((results) => {
        if (cancelled) return;
        const [memRes, metricsRes] = results;

        // 记忆接口
        if (memRes.status === 'fulfilled') {
          setUserMemory(memRes.value as UserMemoryResponse);
        } else {
          setUserMemory(null);
        }

        // 指标接口
        if (metricsRes.status === 'fulfilled') {
          setUserMetrics(metricsRes.value);
        } else {
          setUserMetrics(null);
        }

        // 任一失败则提示错误（聚合错误信息）
        const errs: string[] = [];
        if (memRes.status === 'rejected') {
          const reason = memRes.reason;
          errs.push(`记忆: ${reason?.error || reason?.message || '请求失败'}`);
        }
        if (metricsRes.status === 'rejected') {
          const reason = metricsRes.reason;
          errs.push(`指标: ${reason?.error || reason?.message || '请求失败'}`);
        }
        setError(errs.length > 0 ? errs.join('；') : null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [userId, setUserMemory, setUserMetrics]);

  // ---- 指标定义 Table 列定义（13.2）----
  const metricColumns = useMemo<TableColumnsType<MetricDefinition>>(
    () => [
      {
        title: '名称',
        dataIndex: 'name',
        key: 'name',
        width: 160,
        ellipsis: true,
        render: (v: unknown) =>
          v ? <Text strong>{String(v)}</Text> : <Text type="secondary">-</Text>,
      },
      {
        title: '描述',
        dataIndex: 'description',
        key: 'description',
        ellipsis: true,
        render: (v: unknown) =>
          v ? <Text>{String(v)}</Text> : <Text type="secondary">-</Text>,
      },
      {
        title: 'SQL 模式',
        dataIndex: 'sql_pattern',
        key: 'sql_pattern',
        width: 240,
        ellipsis: true,
        render: (v: unknown) =>
          v ? (
            <Text code style={{ fontSize: 12 }}>
              {String(v)}
            </Text>
          ) : (
            <Text type="secondary">-</Text>
          ),
      },
      {
        title: '来源',
        dataIndex: 'source',
        key: 'source',
        width: 120,
        ellipsis: true,
        render: (v: unknown) =>
          v ? <Tag>{String(v)}</Tag> : <Text type="secondary">-</Text>,
      },
      {
        title: '置信度',
        dataIndex: 'confidence',
        key: 'confidence',
        width: 110,
        align: 'center',
        render: (v: unknown) => {
          if (v === null || v === undefined) {
            return <Text type="secondary">-</Text>;
          }
          const num = typeof v === 'number' ? v : Number(v);
          if (Number.isNaN(num)) return <Text type="secondary">-</Text>;
          // 0~1 视为百分比，否则按数值展示
          const pct = num <= 1 ? num * 100 : num;
          const color = pct >= 80 ? 'green' : pct >= 50 ? 'orange' : 'red';
          return <Tag color={color}>{pct.toFixed(1)}%</Tag>;
        },
      },
    ],
    [],
  );

  const metrics: MetricDefinition[] = userMetrics?.metrics ?? [];

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto' }}>
      <Title level={4} style={{ marginTop: 0 }}>
        用户记忆
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 16 }}>
        展示用户「{userId}」的长期记忆与指标定义。数据来自{' '}
        <Text code>/api/v1/users/&#123;user_id&#125;/memory</Text> 与{' '}
        <Text code>/api/v1/users/&#123;user_id&#125;/metrics</Text>。
      </Paragraph>

      {/* 加载中：覆盖整区域 Spin */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin tip="加载用户记忆中..." size="large" />
        </div>
      ) : error ? (
        <Alert
          type="error"
          showIcon
          message="加载用户记忆失败"
          description={error}
          style={{ marginBottom: 16 }}
        />
      ) : null}

      {/* 非加载态展示数据；即便有部分错误，已成功的数据仍展示 */}
      {!loading && userMemory ? (
        <>
          {/* 13.1：分区块展示 userMemory 各字段 */}
          <Card size="small" style={{ marginBottom: 16 }}>
            <SectionTitle>术语偏好（term_preferences）</SectionTitle>
            <ObjectBlock data={userMemory.term_preferences ?? {}} />
          </Card>

          <Card size="small" style={{ marginBottom: 16 }}>
            <SectionTitle>常用表（frequently_used_tables）</SectionTitle>
            <ObjectBlock data={userMemory.frequently_used_tables ?? {}} />
          </Card>

          <Card size="small" style={{ marginBottom: 16 }}>
            <SectionTitle>指标定义（metric_definitions）</SectionTitle>
            <ObjectBlock data={userMemory.metric_definitions ?? {}} />
          </Card>

          <Card size="small" style={{ marginBottom: 16 }}>
            <SectionTitle>查询偏好（query_preferences）</SectionTitle>
            <ObjectBlock data={userMemory.query_preferences ?? {}} />
          </Card>

          <Card size="small" style={{ marginBottom: 16 }}>
            <SectionTitle>领域上下文（domain_context）</SectionTitle>
            <ObjectBlock data={userMemory.domain_context ?? {}} />
          </Card>

          <Card size="small" style={{ marginBottom: 16 }}>
            <SectionTitle>反问历史（clarification_history）</SectionTitle>
            <ArrayBlock data={userMemory.clarification_history ?? []} />
          </Card>
        </>
      ) : null}

      {/* 13.2：指标定义列表 Table（与记忆区块并列，仅在有数据且非加载时展示） */}
      {!loading && metrics.length > 0 ? (
        <Card size="small" style={{ marginBottom: 16 }}>
          <SectionTitle>指标定义列表（userMetrics.metrics）</SectionTitle>
          <Table<MetricDefinition>
            columns={metricColumns}
            dataSource={metrics}
            rowKey={(_record, index) => String(index)}
            size="small"
            pagination={{ pageSize: 10, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }}
            scroll={{ x: 'max-content' }}
            locale={{ emptyText: <Empty description="暂无" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          />
        </Card>
      ) : null}

      {/* 空态：既无记忆也无指标且无错误 -> 全局 Empty */}
      {!loading && !error && !userMemory && metrics.length === 0 ? (
        <Empty description="暂无用户记忆数据" />
      ) : null}
    </div>
  );
}
