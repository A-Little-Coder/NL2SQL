/**
 * DB 选择器（任务 7.1-7.2）
 *
 * 职责：
 * - 从 store.dbList 填充 AntD Select：每项 label=`${db_id}`（附 db_path 简短），value=db_id
 * - 值绑定 store.selectedDbId，onChange 调 setSelectedDbId
 * - dbList 为空时 placeholder="无可用数据库"
 * - 7.3 的 404 回退由 Conversation 在查询失败时处理，DbSelector 只管受控选择
 *
 * store 依赖：dbList / selectedDbId / setSelectedDbId
 */
import { Select, Tooltip } from 'antd';
import type { DatabaseInfo } from '@/api/types';
import { useChatStore } from '@/store/useChatStore';

/** 截断 db_path 仅保留文件名，避免下拉项过长 */
function shortPath(p: string): string {
  if (!p) return '';
  // 取最后一段路径（兼容 / 与 \）
  const parts = p.split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

export default function DbSelector() {
  // ---- 从 store 读取所需切片（selector 避免整体重渲染）----
  const dbList = useChatStore((s) => s.dbList);
  const selectedDbId = useChatStore((s) => s.selectedDbId);
  const setSelectedDbId = useChatStore((s) => s.setSelectedDbId);

  const options = dbList.map((db: DatabaseInfo) => ({
    label: (
      <Tooltip title={db.db_path} placement="right">
        <span>
          {db.db_id}
          {db.db_path ? <span style={{ color: '#999', marginLeft: 6 }}>({shortPath(db.db_path)})</span> : null}
        </span>
      </Tooltip>
    ),
    // AntD Select 用 value 作受控键，必须为 string
    value: db.db_id,
  }));

  return (
    <Select
      style={{ width: 220 }}
      placeholder="无可用数据库"
      value={selectedDbId ?? undefined}
      onChange={(val: string) => setSelectedDbId(val)}
      options={options}
      loading={false}
      // 空列表时禁用，避免误操作
      disabled={dbList.length === 0}
      showSearch
      optionFilterProp="value"
    />
  );
}
