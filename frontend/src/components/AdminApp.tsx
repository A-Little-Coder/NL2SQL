import { useCallback, useEffect, useState } from 'react';
import {
  Button,
  Card,
  Form,
  Input,
  Layout,
  message,
  Select,
  Space,
  Table,
  Tabs,
  Typography,
  type TableColumnsType,
} from 'antd';
import {
  addDenyRule,
  bindUserRole,
  createRole,
  createUser,
  deleteDenyRule,
  getEffectivePermissions,
  listDenyRules,
  listRoles,
  listUserRoles,
  listUsers,
  type AdminUser,
  type DenyRule,
  type EffectivePermission,
  type Role,
} from '../api/admin';

const { Header, Content } = Layout;
const { Title } = Typography;

// ---------- 角色管理 ----------
function RolesPanel() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [form] = Form.useForm();
  const load = useCallback(async () => {
    try {
      setRoles((await listRoles()).roles);
    } catch {
      message.error('加载角色失败');
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const onSubmit = async (v: Role) => {
    await createRole(v);
    message.success('角色已创建');
    form.resetFields();
    load();
  };
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Form form={form} layout="inline" onFinish={onSubmit} data-testid="role-form" name="roleForm">
        <Form.Item name="role_id" label="角色ID" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="name" label="名称" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit">
            新增
          </Button>
        </Form.Item>
      </Form>
      <Table
        rowKey="role_id"
        dataSource={roles}
        pagination={false}
        columns={[
          { title: '角色ID', dataIndex: 'role_id' },
          { title: '名称', dataIndex: 'name' },
        ]}
      />
    </Space>
  );
}

// ---------- 员工管理 ----------
function UserRolesBinder({ roles }: { roles: Role[] }) {
  const [userId, setUserId] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [current, setCurrent] = useState<string[]>([]);
  const onLoad = async () => {
    if (!userId) return;
    const r = await listUserRoles(userId);
    setCurrent(r.roles);
    setSelected(r.roles);
  };
  const onSave = async () => {
    for (const rid of selected) await bindUserRole(userId, rid);
    message.success('角色已绑定');
    onLoad();
  };
  return (
    <Card title="角色绑定" size="small" data-testid="role-binder">
      <Space>
        <Input
          placeholder="员工ID"
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          style={{ width: 160 }}
        />
        <Button onClick={onLoad}>查询</Button>
        <Select
          mode="multiple"
          style={{ minWidth: 240 }}
          value={selected}
          onChange={setSelected}
          options={roles.map((r) => ({ label: r.name, value: r.role_id }))}
        />
        <Button type="primary" onClick={onSave}>
          保存绑定
        </Button>
      </Space>
      {current.length > 0 && (
        <div style={{ marginTop: 8 }}>当前角色：{current.join(', ')}</div>
      )}
    </Card>
  );
}

function UsersPanel() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [form] = Form.useForm();
  const load = useCallback(async () => {
    try {
      setUsers((await listUsers()).users);
      setRoles((await listRoles()).roles);
    } catch {
      message.error('加载失败');
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const onSubmit = async (v: AdminUser) => {
    await createUser(v);
    message.success('员工已创建');
    form.resetFields();
    load();
  };
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Form form={form} layout="inline" onFinish={onSubmit} data-testid="user-form" name="userForm">
        <Form.Item name="user_id" label="员工ID" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="name" label="姓名" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="dept" label="部门">
          <Input />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit">
            新增
          </Button>
        </Form.Item>
      </Form>
      <Table
        rowKey="user_id"
        dataSource={users}
        pagination={false}
        columns={[
          { title: '员工ID', dataIndex: 'user_id' },
          { title: '姓名', dataIndex: 'name' },
          { title: '部门', dataIndex: 'dept' },
        ]}
      />
      <UserRolesBinder roles={roles} />
    </Space>
  );
}

// ---------- 黑名单配置 ----------
function DenyRulesPanel() {
  const [rules, setRules] = useState<DenyRule[]>([]);
  const [form] = Form.useForm();
  const load = useCallback(async () => {
    try {
      setRules((await listDenyRules()).rules);
    } catch {
      message.error('加载失败');
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const onSubmit = async (v: DenyRule) => {
    await addDenyRule({
      ...v,
      column_pattern: v.column_pattern || null,
      reason: v.reason || null,
    });
    message.success('规则已添加');
    form.resetFields();
    load();
  };
  const onDelete = async (id: number) => {
    await deleteDenyRule(id);
    message.success('已删除');
    load();
  };
  const columns: TableColumnsType<DenyRule> = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '库', dataIndex: 'db_id' },
    { title: '角色', dataIndex: 'role_id' },
    { title: '表模式', dataIndex: 'table_pattern' },
    {
      title: '列模式',
      dataIndex: 'column_pattern',
      render: (v: string | null) => v || '(整表禁)',
    },
    { title: '原因', dataIndex: 'reason' },
    {
      title: '操作',
      render: (_: unknown, r: DenyRule) => (
        <Button danger size="small" onClick={() => onDelete(r.id!)}>
          删除
        </Button>
      ),
    },
  ];
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Form form={form} layout="inline" onFinish={onSubmit} data-testid="rule-form" name="ruleForm">
        <Form.Item name="db_id" label="库ID" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="role_id" label="角色ID" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="table_pattern" label="表模式" rules={[{ required: true }]}>
          <Input placeholder="如 employees 或 *" />
        </Form.Item>
        <Form.Item name="column_pattern" label="列模式">
          <Input placeholder="如 salary，留空=整表禁" />
        </Form.Item>
        <Form.Item name="reason" label="原因">
          <Input />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit">
            添加
          </Button>
        </Form.Item>
      </Form>
      <Table rowKey="id" dataSource={rules} columns={columns} pagination={false} />
    </Space>
  );
}

// ---------- 有效权限查询 ----------
function PermsPanel() {
  const [userId, setUserId] = useState('');
  const [dbId, setDbId] = useState('');
  const [rules, setRules] = useState<EffectivePermission[]>([]);
  const [loaded, setLoaded] = useState(false);
  const onQuery = async () => {
    if (!userId || !dbId) return;
    const r = await getEffectivePermissions(userId, dbId);
    setRules(r.deny_rules);
    setLoaded(true);
  };
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Space data-testid="perms-form">
        <Input
          placeholder="员工ID"
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          style={{ width: 160 }}
        />
        <Input
          placeholder="库ID"
          value={dbId}
          onChange={(e) => setDbId(e.target.value)}
          style={{ width: 160 }}
        />
        <Button type="primary" onClick={onQuery}>
          查询
        </Button>
      </Space>
      {loaded && (
        <Table
          rowKey={(_, i) => String(i)}
          dataSource={rules}
          pagination={false}
          columns={[
            { title: '表模式', dataIndex: 'table_pattern' },
            {
              title: '列模式',
              dataIndex: 'column_pattern',
              render: (v: string | null) => v || '(整表禁)',
            },
            { title: '原因', dataIndex: 'reason' },
          ]}
        />
      )}
    </Space>
  );
}

export default function AdminApp() {
  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center' }}>
        <Title level={3} style={{ color: '#fff', margin: 0 }}>
          权限管理后台
        </Title>
      </Header>
      <Content style={{ padding: 24 }}>
        <Tabs
          items={[
            { key: 'roles', label: '角色管理', children: <RolesPanel /> },
            { key: 'users', label: '员工管理', children: <UsersPanel /> },
            { key: 'rules', label: '黑名单配置', children: <DenyRulesPanel /> },
            { key: 'perms', label: '有效权限查询', children: <PermsPanel /> },
          ]}
        />
        <div style={{ marginTop: 16 }}>
          <a href="/">← 返回问数前台</a>
        </div>
      </Content>
    </Layout>
  );
}
