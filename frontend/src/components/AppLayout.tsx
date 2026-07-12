/**
 * 三栏可拖拽布局（D6/D7，change enhance-ir-display-and-layout）
 *
 * 用 react-resizable-panels 替代 AntD Sider：
 *   - 左右栏宽度可拖拽调整，autoSaveId 持久化到 localStorage
 *   - 左右栏 collapsible：拖到 minSize 以下折叠为窄展开条，内容组件 unmount
 *   - 中栏占剩余空间
 *   - 窄于阈值时只渲染 CollapsedBar，不压扁内容
 */
import { useEffect, useRef, useState } from 'react';
import { Layout, Input, Segmented, Spin, Typography, Button } from 'antd';
import { MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import type { ImperativePanelHandle } from 'react-resizable-panels';
import SessionSidebar from './SessionSidebar';
import DbSelector from './DbSelector';
import Conversation from './Conversation';
import DetailInspector from './DetailInspector';
import UserMemoryView from './UserMemoryView';
import { useChatStore } from '@/store/useChatStore';
import { listDatabases } from '@/api/rest';
import './AppLayout.css';

const { Header, Content } = Layout;
const { Title } = Typography;

/** 折叠态窄展开条（D7）：仅一个展开按钮，原内容组件已 unmount */
export function CollapsedBar({
  side,
  onExpand,
}: {
  side: 'left' | 'right';
  onExpand: () => void;
}) {
  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#fafafa',
        borderRight: side === 'left' ? '1px solid #f0f0f0' : 'none',
        borderLeft: side === 'right' ? '1px solid #f0f0f0' : 'none',
      }}
    >
      <Button
        type="text"
        icon={side === 'left' ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
        onClick={onExpand}
        aria-label={side === 'left' ? '展开会话侧栏' : '展开详情检查器'}
        title={side === 'left' ? '展开会话侧栏' : '展开详情检查器'}
      />
    </div>
  );
}

export default function AppLayout() {
  const userId = useChatStore((s) => s.userId);
  const setUserId = useChatStore((s) => s.setUserId);
  const viewMode = useChatStore((s) => s.viewMode);
  const setViewMode = useChatStore((s) => s.setViewMode);
  const setDbList = useChatStore((s) => s.setDbList);
  const setSelectedDbId = useChatStore((s) => s.setSelectedDbId);

  const leftRef = useRef<ImperativePanelHandle>(null);
  const rightRef = useRef<ImperativePanelHandle>(null);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const [initLoading, setInitLoading] = useState(true);

  // 初始加载：数据库列表
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
        // 后端可能未启动，静默；DbSelector 显示空态
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
      <Layout style={{ height: 'calc(100vh - 64px)' }}>
        <PanelGroup direction="horizontal" autoSaveId="nl2sql-layout-v2" style={{ height: '100%' }}>
          {/* 左栏：会话侧栏 */}
          <Panel
            ref={leftRef}
            defaultSize={20}
            minSize={6}
            maxSize={30}
            collapsible
            collapsedSize={0}
            onResize={(size) => setLeftCollapsed(size === 0)}
          >
            {leftCollapsed ? (
              <CollapsedBar side="left" onExpand={() => leftRef.current?.expand()} />
            ) : (
              <div className="sider-inner">
                <SessionSidebar />
              </div>
            )}
          </Panel>
          <PanelResizeHandle className="resize-handle" />

          {/* 中栏：对话 / 用户记忆 */}
          <Panel>
            <Content
              style={{
                height: '100%',
                padding: 16,
                overflow: 'auto',
                background: '#f5f5f5',
              }}
            >
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
          </Panel>

          <PanelResizeHandle className="resize-handle" />
          {/* 右栏：详情检查器 */}
          <Panel
            ref={rightRef}
            defaultSize={32}
            minSize={6}
            maxSize={45}
            collapsible
            collapsedSize={0}
            onResize={(size) => setRightCollapsed(size === 0)}
          >
            {rightCollapsed ? (
              <CollapsedBar side="right" onExpand={() => rightRef.current?.expand()} />
            ) : (
              <div className="sider-inner">
                <DetailInspector />
              </div>
            )}
          </Panel>
        </PanelGroup>
      </Layout>
    </Layout>
  );
}
