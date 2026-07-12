## Context

`frontend-ui` change 上线后，实际使用暴露四处问题，均与"透明度"与"交互"相关：

1. **反问双发 error**：`src/graph/main_graph.py` 的 `_wrap_node` 装饰器（line 96-98）`except Exception` 捕获所有异常并 `emit_safe("error", ...)`。`cache_confirm`/`task_planner` 反问时调用 `langgraph.types.interrupt()`，该函数抛出 `GraphInterrupt`（继承 `Exception`），被此处捕获并 emit error，随后 re-raise；`query.py` 的 `graph.stream` 循环检测到 `__interrupt__` 又 emit `clarification`。前端 reducer 先收到 error（置 `status='error'`）再收到 clarification（置 `status='awaiting_clarification'`），时间轴上同时出现红色错误节点与反问节点。

2. **IR 召回数据链路断裂**：
   - `RetrievedContext`（`information_retrieval.py:90`）真实字段为 `tables`/`columns`/`values`/`keywords`/`keyword_groups`/`keyword_columns_map`/`lsh_hit_count`/`vector_top_scores`。
   - `_summarize_schema`（`main_graph.py:537-552`）却取 `getattr(ctx, "schema_results", None)`——该字段不存在，恒返回 `None`，`or {}` 成空 dict，循环不执行，`schema_recall` 事件永远发 `{"groups": []}`。
   - `retrieve_values`（`information_retrieval.py:274`）接收扁平化 `all_keywords`（所有组的 terms 合并），内部 `for keyword in keywords:` 明知命中 term，但塞入 `RetrievedItem` 时只存 `name/table_name/score/metadata{column_name,...}`，**丢弃了 keyword 与组归属**。
   - 前端 `IrDetail` 收到空 `schemaRecall`，且从无 `values` 数据，故"字段召回""值召回"均不可见。

3. **SS 阶段时间轴缺失**：`single_query_graph.py` 注册 `ss` 与 `schema_finalize` 节点，`_wrap_node` 发 `stage` 事件；但前端 `mapStageNode`（`reducer.ts:61-79`）只映射 ir/cg/execution/decision/answerability，无 `ss`/`schema` 分支，返回 `null`，事件被忽略。

4. **三栏布局僵化**：`AppLayout.tsx` 用 AntD `<Sider width={280/440} collapsible>`，固定宽度 + 二态折叠（0 或全宽），无法拖拽调宽；折叠到 0 前的中间态会压扁边栏内容。

## Goals / Non-Goals

**Goals**：
- 反问场景不再出现 error 事件，时间轴干净地停在反问节点
- IR 详情按关键词组展示「phrase + 同义词 + 召回字段 + 召回值」，值召回归属准确（后端记录，前端零猜测）
- SS 阶段在时间轴上独立可见
- 三栏宽度可拖拽调整，窄于阈值时隐藏内容（不压扁），宽度记忆

**Non-Goals**：
- 不改 IR 检索算法（LSH 粗召回 + 语义精排逻辑不变）
- 不改 SS schema 选择算法
- 不做结果图表/数据可视化
- 不做 SQL 手改 workbench
- 不做生产部署托管
- 不重做整体视觉风格（仅布局可调 + IR 详情重构 + 窄边栏隐藏）

## Decisions

### D1 反问 error 双发修复：`_wrap_node` 放行 GraphInterrupt

`_wrap_node` 的 `except Exception as e:` 分支新增判断：若 `e` 是 `GraphInterrupt`，直接 `raise` 不 emit error。`GraphInterrupt` 是 langgraph 的正常控制流信号，不是错误。

```python
except Exception as e:
    # GraphInterrupt 是 interrupt() 的正常控制流信号，非错误，放行不 emit error
    if _is_graph_interrupt(e):
        raise
    logger.exception(f"[qid={qid}] [stage] node={node_name} error={e!r}")
    emit_safe("error", {"node": node_name, "error": str(e)})
    raise
```

`_is_graph_interrupt` 用 try/except import `langgraph.errors.GraphInterrupt`，回退到与已 import 的 `interrupt` 函数所属模块的异常类型比较（`interrupt` 在 main_graph 顶部已 import，None 时降级）。

**备选**：在 `query.py` 的 `except Exception` 里判断 GraphInterrupt 不 emit error。但 `_wrap_node` 是更早的捕获点，且 `query.py` 的 except 还要处理真实异常，不如在源头放行干净。

### D2 值召回组归属：后端 `retrieve_values` 记录 `source_phrase`（非前端硬归属）

前端按 `table.column` 反查 `keyword_columns_map` 做归属**必然出错**（三类场景：一列多组、value 列不在任何组、enhance 反向补列悬空）。正确做法是后端在检索时就记下归属：

- `retrieve()` 主流程构建 `term -> phrase` 映射，传给 `retrieve_values`
- `retrieve_values` 内部 `for keyword in keywords:` 已知当前 keyword，查映射得 phrase，写入 `metadata["source_phrase"]` 与 `metadata["source_term"]`
- 一个 value 被多个 term 命中时：`seen` 去重时保留 LSH `jaccard_score` 最高的 term 所属 phrase（覆盖更新）

**向后兼容**：`metadata` 是 dict，加字段不影响其他消费方（`enhance_with_schema` 等只读已有字段）。

### D3 `schema_recall` 事件重构：按关键词组聚合

`_summarize_schema` 弃用不存在的 `schema_results`，改基于真实字段聚合：

```python
def _summarize_schema(ctx) -> dict:
    keyword_groups = []
    col_detail = {f"{c.table_name}.{c.name}": c for c in (ctx.columns or [])}
    for g in (ctx.keyword_groups or []):
        phrase = g.phrase
        col_keys = (ctx.keyword_columns_map or {}).get(phrase, [])
        columns = [
            {"table": k.split(".", 1)[0], "column": k.split(".", 1)[1],
             "score": col_detail[k].score}
            for k in col_keys if k in col_detail
        ]
        values = [
            {"value": v.name, "table": v.table_name,
             "column": v.metadata.get("column_name"), "score": v.score}
            for v in (ctx.values or [])
            if v.metadata.get("source_phrase") == phrase
        ]
        keyword_groups.append({"phrase": phrase, "terms": list(g.terms or []),
                               "columns": columns, "values": values})
    return {"keyword_groups": keyword_groups}
```

**契约破坏性**：`groups` -> `keyword_groups`，结构全变。但唯一消费方是本前端，同步更新 `api/types.ts` + `reducer.ts` + `IrDetail`。`schema_recall` 事件不再有顶层 `groups`。

### D4 SS 时间轴节点：`mapStageNode` 增补 ss 映射

- `TimelineNodeType` 联合类型加 `'ss'`
- `mapStageNode` 增 `if (n === 'ss' || n.includes('schema_select')) return 'ss';`
- `schema_finalize` 节点归入 `ss`（同属 schema 选择阶段，不单列），避免时间轴节点过多；其 `stage` 事件更新 `ss` 节点 summary
- `NODE_LABEL` 加 `ss: 'Schema 选择'`
- `AgentTimeline` 加 ss 节点图标/颜色
- `DetailInspector` 加 `SsDetail`：展示 selected_schema 表/列数、join_edges、bridge_tables（来自 `schema_finalize` 事件）

**`schema_finalize` 事件**：后端已 emit `schema_finalize`（`main_graph.py:749`，payload `{join_edges, bridge_tables}`），前端当前未处理。reducer 增 `case 'schema_finalize'`：存入 `turn.details.schemaFinalize`，并更新 `ss` 时间轴节点 summary（如"SS · join 边 N · 桥接表 M"）。

### D5 IR 详情按关键词组聚合展示

`IrDetail` 重构为逐组渲染：

```
关键词组：各科score
  同义词：各科score · 各科成绩 · subject score
  召回字段 (3)：satscores.AvgScrRead 0.92 · satscores.AvgScrMath 0.88 · ...
  召回值 (0)：（无）

关键词组：学校总数
  同义词：学校总数 · 学校数量 · school count
  召回字段 (1)：schools.school_id 0.85
  召回值 (2)：Lincoln High (schools.school_name) 0.78 · Roosevelt ... 0.71
```

- 每组一个 Card（组多时用 Collapse 折叠，默认展开首个）
- phrase 作标题，terms 作次行 Tag
- 召回字段：Tag 列表或小表格，带 score
- 召回值：Tag 列表或小表格，带 value/table.column/score
- 空组（无字段无值）也展示，标注"无召回"

**数据来源**：`turn.details.ir.keywordGroups`（新结构），前端不做任何归属计算，纯渲染后端 `source_phrase` 已归属好的 values。

### D6 三栏可拖拽：`react-resizable-panels`

弃用 AntD `<Sider>`，改用 `react-resizable-panels` 的 `PanelGroup` + `Panel` + `PanelResizeHandle`：

- 左栏：min 80px / max 400px，default 280px
- 中栏：flex（占剩余）
- 右栏：min 80px / max 600px，default 440px
- `PanelResizeHandle` 自定义样式为竖向分隔条（hover 高亮）
- `autoSaveId` 持久化宽度到 localStorage（库内置）

**备选**：自研 mousedown/move/up（控制力强但工作量大、需处理触摸/边界）；保留 AntD Sider 加拖拽（Sider width 是 prop，强改触发重渲染抖动）。`react-resizable-panels` 轻量（~3KB）、API 简洁、原生支持 collapsible + autoSave，最优。

### D7 窄边栏隐藏内容：阈值以下不 mount children

`Panel` 的 `collapsible` + `collapsedSize` 实现：当用户把分隔条拖到 min 以下，panel 折叠到 `collapsedSize`（0）。配合条件渲染：

```tsx
<Panel collapsible collapsedSize={0} minSize={...} ref={leftRef}>
  {leftCollapsed ? <窄展开条 onClick={expand}/> : <SessionSidebar/>}
</Panel>
```

- 拖拽到 min 以下 -> `collapsed=true` -> 只渲染展开条（►），`SessionSidebar` 完全 unmount
- 点击展开条 -> 调用 `panel.expand()` 恢复上次宽度
- **中间态不压扁**：配合 collapsible，实际只有"正常宽"或"折叠"两态，无压扁中间态

**阈值**：minSize 用百分比，按 80px / 视口宽换算（如视口 1440px 时 minSize≈6%）。`react-resizable-panels` 的 `Panel` 提供 `imperativeRef` 可读 `collapsed` 状态并调 `expand()`。

## Risks

- **`schema_recall` 契约破坏性变更**：若有第三方消费该事件会受影响。当前仅本前端消费，且后端 Swagger 文档需同步更新。mitigation：同步更新 `query.py` docstring 与前端 `api/types.ts`。
- **`retrieve_values` 改动影响现有测试**：若有测试断言 `RetrievedItem.metadata` 精确等于某 dict，加字段会失败。mitigation：跑全量 `retrieve_values` 相关测试，按需更新断言。
- **`react-resizable-panels` 新依赖**：增加前端 bundle ~3KB，可接受；需确认与 React 18 兼容（已确认，库支持 React 16.8+）。
- **GraphInterrupt import 路径**：`langgraph.errors.GraphInterrupt` 在不同 langgraph 版本位置可能不同。mitigation：用 try/except import，回退到与已 import 的 `interrupt` 比较。
