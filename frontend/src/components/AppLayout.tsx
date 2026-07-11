/**
 * 三栏布局骨架（决策 D5/D9，任务 5.1-5.3）
 *
 * AntD Layout：
 *   Header  -- 标题 + DB 选择器 + user_id 输入 + 视图切换（对话/用户记忆）
 *   Sider 左 -- 会话侧栏（可折叠）
 *   Content -- 对话区（viewMode=chat）或 用户记忆视图（viewMode=memory）
 *   Sider 右 -- 节点详情检查器（可折叠）
 *
 * 初始加载：listDatabases 填充 dbList，selectedDbId 默认取首个。
 * （sessions 由 SessionSidebar 自行按 userId 加载，userId 切换时自动刷新）
 */
import { useEffect, useState } from 'react';
import { Layout, Input, Segmented, Spin, Typography } from 'antd';
import SessionSidebar from './SessionSidebar';
import DbSelector from './DbSelector';
import Conversation from './Conversation';
import DetailInspector from './DetailInspector';
import UserMemoryView from './UserMemoryView';
import { useChatStore } from '@/store/useChatStore';
import { listDatabases } from '@/api/rest';

const { Header, Sider, Content } = Layout;
const { Title } = Typography;

export default function AppLayout() {
  const userId = useChatStore((s) => s.userId);
  const setUserId = useChatStore((s) => s.setUserId);
  const viewMode = useChatStore((s) => s.viewMode);
  const setViewMode = useChatStore((s) => s.setViewMode);
  const setDbList = useChatStore((s) => s.setDbList);
  const setSelectedDbId = useChatStore((s) => s.setSelectedDbId);

  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const [initLoading, setInitLoading] = useState(true);

  // 初始加载：数据库列表（任务 5.3）
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await listDatabases();
        if (cancelled) return;
        setDbList(res.databases ?? []);
        if (res.databases && res.databases.length > 0) {
          setSelectedDbId(res.databases[0].db_id);
        }
      } catch {
        // 后端可能未启动，静默；DbSelector 会显示空态
      } finally {
        if (!cancelled) setInitLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [setDbList, setSelectedDbId]);

  return (
    <Layout style={{ height: '100vh' }}>
      <Header
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          padding: '0 20px',
        }}
      >
        <Title level={4} style={{ color: '#fff', margin: 0, whiteSpace: 'nowrap' }}>
          NL2SQL 问数助手
        </Title>
        <DbSelector />
        <Input
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          placeholder="user_id"
          style={{ width: 160 }}
          prefix={<span style={{ color: '#ccc' }}>user</span>}
        />
        <Segmented
          value={viewMode}
          onChange={(v) => setViewMode(v as 'chat' | 'memory')}
          options={[
            { label: '对话', value: 'chat' },
            { label: '用户记忆', value: 'memory' },
          ]}
        />
      </Header>
      <Layout>
        <Sider
          width={280}
          theme="light"
          collapsible
          collapsed={leftCollapsed}
          onCollapse={setLeftCollapsed}
          style={{ overflow: 'auto' }}
        >
          <SessionSidebar />
        </Sider>
        <Content style={{ padding: 16, overflow: 'auto', background: '#f5f5f5' }}>
          {initLoading ? (
            <div style={{ textAlign: 'center', padding: 48 }}>
              <Spin />
              <div style={{ marginTop: 8, color: '#888' }}>加载中…</div>
            </div>
          ) : viewMode === 'memory' ? (
            <UserMemoryView />
          ) : (
            <Conversation />
          )}
        </Content>
        <Sider
          width={440}
          theme="light"
          collapsible
          collapsed={rightCollapsed}
          onCollapse={setRightCollapsed}
          reverseArrow
          style={{ overflow: 'auto' }}
        >
          <DetailInspector />
        </Sider>
      </Layout>
    </Layout>
  );
}
