# 表/字段权限管理（table-field-acl）

## 概述

对员工按**表级 + 字段级**做访问控制。敏感字段默认**脱敏**（非直接拒答），仅当某关键词召回字段**全部无权限**时反问用户是否脱敏继续。采用 RBAC + 黑名单模型。

仅表级 + 字段级；字段值/行级权限预留 `auth/field_value_acl.db` 未实现。

## 架构

```
single_query_graph:
  ir -> ss -> [permission] -> schema_finalize -> ... -> cg -> execution -> decision -> [mask] -> END
              ↑ SS 后关键词级判断                    ↑ decision 后脱敏
```

- **权限节点**（SS 之后）：以关键词为单元，回捞 `retrieved_context.keyword_columns_map`（phrase->["table.column"]）。
  - 部分无权：剔除无权字段（空表整表移除），不脱敏；
  - 全无权：`interrupt` 反问 `permission_choice`（脱敏继续/放弃）。mask 保留字段参与生成，reject 拒答。
- **脱敏节点**（decision 之后）：解析 `final_sql` 涉及字段（含聚合/别名，sqlglot），黑名单命中列按**位置对齐**结果列替换为 `***`。不依赖权限节点传标记，覆盖主路径与 cache_hit 路径。解析失败用列名字符串匹配兜底脱敏。
- **方案 B**：`single_query_graph` 作为 subgraph 编译进 `main_graph`（`run_single_query` 节点直接用 compiled graph），子图内 interrupt 被主图 checkpointer（InMemorySaver）接管，一次跑完、可 resume。
- **多意图降级**：`SubqueryOrchestrator` 仍用 invoke（不支持子查询级 interrupt），子查询全无权时直接拒答该子查询（注入 `_multi_intent` 标记），不反问。

## RBAC 黑名单模型

黑名单存"禁止访问的表/字段"，其余默认放行。多角色取并集（只禁不放开，并集即最严）。表级规则（`column_pattern=NULL`）展开为该表所有字段无权限。通配用 fnmatch（`*` 匹配任意）。

## auth 元数据

独立元数据库 `auth/table_field_acl.db`（与业务库隔离，带 `db_id` 维度适配多库）：

```sql
roles(role_id, name)
users(user_id, name, dept)
user_roles(user_id, role_id)          -- 多角色
deny_rules(id, db_id, role_id, table_pattern, column_pattern, reason)
-- column_pattern 为 NULL 表示整表禁
```

初始化：`python -m src.permission.init_auth`（建表 + 演示种子）。

## 开关

环境变量 `TABLE_FIELD_ACL_ENABLED`（默认 `false`）。关闭时权限/脱敏节点直通，流水线完全恢复原状（向后兼容）。

## REST API（`/api/v1/admin/*`）

- `POST/GET /admin/roles`、`POST/GET /admin/users`
- `POST/GET /admin/users/{id}/roles`（角色绑定）
- `POST/GET /admin/deny_rules`、`DELETE /admin/deny_rules/{id}`
- `GET /admin/permissions?user_id=&db_id=`（有效黑名单查询）

## 前端 `/admin`

`App.tsx` 按 `window.location.pathname` 条件渲染（`/admin` -> `AdminApp`，无新路由依赖）。4 个 Tab：角色管理 / 员工管理（含角色绑定）/ 黑名单配置 / 有效权限查询。

## 测试

- 后端 pytest：PolicyStore(9) + subgraph interrupt(2) + SQL 解析(8) + 权限节点(5) + 脱敏节点(6) + admin API(4) + 全量回归 725
- 前端 vitest：admin API client(9) + 全量 88
- Playwright E2E：`e2e/admin-crud.spec.ts`（后台 CRUD 全流程）

## 非目标

字段值/行级权限、真登录/token 认证、SQL 层脱敏、安全护栏（真实值内存暂存/日志脱敏）。
