/**
 * 结果表格（任务 12.1 / 12.3，决策 D7）
 *
 * 职责：
 * - turn.result 为 null 时不渲染
 * - 上方放 SqlBlock 展示 SQL 代码块（含复制按钮）
 * - 取首行 keys 作列定义，AntD Table 分页（默认 10/页）
 * - 空结果（rows 为空）：Table 显示"无数据"占位（locale.emptyText），SqlBlock 仍展示
 *
 * props: { turn: Turn }
 */
import { useMemo } from 'react';
import { Empty, Table, type TableColumnsType } from 'antd';
import type { Turn } from '@/store/types';
import SqlBlock from '../SqlBlock';

export default function ResultTable({ turn }: { turn: Turn }) {
  // 无 result 直接不渲染
  if (!turn.result) return null;

  const { sql, rows } = turn.result;

  // 取首行 keys 作列；rows 为空时 columns 为空数组（Table 显示"无数据"占位）
  const columns = useMemo<TableColumnsType<Record<string, unknown>>>(() => {
    if (!rows || rows.length === 0) return [];
    const firstRow = rows[0];
    return Object.keys(firstRow).map((key) => ({
      title: key,
      dataIndex: key,
      key,
      ellipsis: true,
    }));
  }, [rows]);

  return (
    <div style={{ marginTop: 12 }}>
      {/* SQL 代码块置于表格上方 */}
      <SqlBlock sql={sql} />
      {/* 结果表格，分页默认 10/页；空结果显示"无数据" */}
      <Table<Record<string, unknown>>
        columns={columns}
        dataSource={rows}
        rowKey={(_record, index) => String(index)}
        size="small"
        pagination={{ pageSize: 10, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }}
        scroll={{ x: 'max-content' }}
        locale={{ emptyText: <Empty description="无数据" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
      />
    </div>
  );
}
