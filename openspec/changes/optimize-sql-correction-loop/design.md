# Design Document: 优化 SQL 纠错反思流程

## Context

NL2SQL 系统目前的执行→纠错→决策链路是**对称且密集**的：5 个候选 SQL 各自独立修复 2 轮，决策时再用多数投票或 LLM 兜底。这种设计在准确性上保守，但在**性能和资源效率上代价过高**。

实际产品使用中我们观察到：
- 即使有候选已经能跑出合理结果，系统仍会"无差别"修复其他失败候选
- `TIMEOUT/PERMISSION` 等本质上 LLM 修不动的错误也会触发 LLM 调用
- LLM 思考链（reasoning_content）以英文输出，前端展示对中文用户体验不佳
- 评分维度单一，把"数据答没答对"与"SQL 写得好不好"混在一起评

本设计文档冻结**两段式评分 + 单候选智能修复**新架构的全部技术决策。

---

## Goals / Non-Goals

### Goals

1. 大幅减少 LLM 调用次数（最坏 25→4，平均降幅 70%+）
2. 修复逻辑由"广撒网"改为"精准修一个"，避免无意义修复
3. 评分模型化、分阶段，决策逻辑可观测可追溯
4. 不可修复错误（TIMEOUT/RUNTIME/PERMISSION）直接跳过 LLM 调用
5. Qwen3 思考链改为中文输出，提升前端展示体验
6. 失败可感知：`fix_failed=True` 让 downstream 能区分"系统救不出来"与"答案不对"

### Non-Goals

1. **不重写 IR/SS/CG 节点** — 本次只优化 Execution + Decision 段
2. **不调整 schema 上下文** — `_try_fix` 修复时仍传全量 schema_text（下一期优化）
3. **不引入新的评分模型** — 复用现有 `LLMClient`，通过 prompt 设计实现两段式评分
4. **不修改 result_verifier** — 保留作为收尾节点
5. **不动 9 个业务 prompt 文件** — 中文思考通过 `LLMClient` 统一注入实现

---

## Architecture Decisions

### Decision 1: 两段式评分（R1 数据视角 / R2 SQL 视角）

**问题**：原方案把 SQL + 结果 + 时间一起给 LLM 评判，评分易受 SQL 形态干扰。

**方案**：拆分为两个独立的评分阶段：
- **R1 (ScoreByData)**：只看数据结果（隐藏 SQL），判断"答案对不对"
- **R2 (ScoreBySQL)**：只看 SQL 代码 + R1 评价，判断"代码写得好不好"

**触发条件**：
- R1 总是触发（有成功候选时）
- R2 **仅在 R1 出现并列最高分=5 时触发**（精挑细选直接返回的候选）

**R1 prompt 关键约束**：
- 不放 SQL 代码（防止 LLM 偷看影响判断）
- 明示"结果为 top-20 节选"
- 失败候选直接剔除

**Alternatives considered**：
- ❌ 单阶段评分：仍存在维度混淆问题
- ❌ R2 在所有 R1<5 时触发：增加 LLM 调用，但收益不明确（既然要修，先修再说）

### Decision 2: 单候选 SmartFix（≤3 轮）

**问题**：5 候选独立各修 2 轮，最坏 10 次 LLM 修复调用，浪费严重。

**方案**：
- 评分后**只选 1 个候选**进入 SmartFix
- 最多 3 轮修复（比原 2 轮多 1 轮，弥补"只修一个"的容错空间）
- 每轮 prompt 携带 `fix_history`，避免反复绕同样的错

**fix_history 数据结构**：
```python
fix_history: List[Dict] = [
    {"round": 1, "sql": "...", "error": "..."},
    {"round": 2, "sql": "...", "error": "..."},
]
```

**Alternatives considered**：
- ❌ 单候选 2 轮：从 10 次降到 2 次太激进，容错空间小
- ❌ 选 top-2 各修 3 轮：仍有冗余，与"精修一个"的目标矛盾

### Decision 3: Decision 节点完全重写

**问题**：保留原 Decision 节点（多数投票分组）会产生职责重叠。

**方案**：`build_decision_graph` 整个重写，原节点全部删除：
- 删除：`group_by_result` / `find_majority` / `select_fastest` / `llm_final` / `all_failed`
- 新增：`score_by_data` / `score_by_sql` / `pick_for_fix` / `smart_fix` / `pick_best_failure` / `verify`

`DecisionResult` 数据结构**字段扩展但接口不变**，向后兼容。

**Alternatives considered**：
- ❌ 在原 Decision 上叠加评分逻辑：留下死代码，职责混乱
- ❌ 保留多数投票作为评分前的预筛：增加复杂度，无明确收益

### Decision 4: 全失败分支按错误等级逐个修

**问题**：全候选失败时盲目修复或直接返回都不理想。

**方案**：错误等级排序（轻→重）：
```
SEMANTIC < SYNTAX < UNKNOWN < TIMEOUT < RUNTIME < PERMISSION
```

- 取**最轻一级的所有候选**，逐个进入 SmartFix
- 任一成功立即返回，剩余不修
- 若最轻级别全是 `TIMEOUT/RUNTIME/PERMISSION` → 直接 `fix_failed=True`（不调 LLM）

**等级依据**：
- `SEMANTIC` (列名/表名错) 最容易让 LLM 修对
- `SYNTAX` 次之
- `UNKNOWN` 不确定但仍可试
- `TIMEOUT/RUNTIME/PERMISSION` 几乎是数据/权限问题，LLM 修不了

**Alternatives considered**：
- ❌ 全部候选都试：浪费 token
- ❌ 只修最轻的第 1 个：错过修复成功的机会
- ❌ LLM 判断哪个最值得修：多一次调用，不如规则

### Decision 5: 中文思考指令在 LLMClient 注入

**问题**：9 个业务 prompt 文件全部要加"用中文思考"会增加维护成本。

**方案**：在 `LLMClient.chat()` 和 `chat_stream()` 入口统一处理：
- 检查 messages 列表第一条是否为 system role
- 若是，**追加**中文思考指令到该 system 消息末尾
- 若不是，**插入**一条新的 system 消息到列表开头

**优点**：
- 一处修改，全局生效
- 9 个业务 prompt 全不动
- CLI/测试/API 走同一路径都生效
- 可通过环境变量 `LLM_CHINESE_THINKING=false` 关闭（默认开启）

**Alternatives considered**：
- ❌ 改 9 个 prompt 文件：散落、易漏
- ❌ 修改 enable_thinking 行为：与 Qwen3 接口绑定，扩展性差

### Decision 6: SmartFix 修复后不再评分

**问题**：修复后的新 SQL 可能不一定比修复前好，是否要再评分确认？

**方案**：**不再评分**，修复成功直接返回。

**理由**：
- 修复后 SQL 至少能执行成功，不存在"执行错误"
- 实操中"修复后变差"是少数情况
- 再评分会多一次 LLM 调用，违反优化目标
- `trace_log` 会记录修复前后的 SQL 差异，便于 debug

**Alternatives considered**：
- ❌ 修复后再走一次 R1：增加 1 次 LLM 调用，收益不明确

### Decision 7: 评分输入包含执行元信息

**输入 schema**：

```
ScoreByData (R1) 输入:
{
  "user_query": "...",
  "candidates": [
    {
      "id": "c1",
      "columns": ["hospital_name", "patient_count"],
      "row_count": 1500,
      "execution_time": 0.05,
      "data_preview": [
        ["xx 医院", 234],
        ...top 20 rows, each cell truncated to 20 chars
      ]
    },
    ...
  ]
}

ScoreBySQL (R2) 输入:
{
  "user_query": "...",
  "candidates": [
    {
      "id": "c1",
      "sql": "SELECT ...",
      "execution_time": 0.05,
      "r1_score": 5,
      "r1_reason": "数据完整..."
    },
    ...
  ]
}
```

让 LLM 能综合判断（比如返回 0 行的候选不该给 5 分）。

### Decision 8: 失败候选评分时直接剔除

**问题**：评分池里要不要包含执行失败的候选？

**方案**：失败候选**直接剔除**，不送入 ScoreByData。

**理由**：
- 失败候选没有结果数据，无法"数据视角"评分
- 减小 prompt 长度
- 若全部失败 → 走全失败分支（已有专门处理）

---

## Data Model Changes

### NL2SQLState 新增字段

```python
class NL2SQLState(TypedDict, total=False):
    # ... 既有字段 ...

    # 评分阶段
    candidate_scores_r1: List[Dict[str, Any]]   # R1 评分 [{id, score, reason}]
    candidate_scores_r2: Optional[List[Dict[str, Any]]]  # R2 评分（条件触发）

    # SmartFix 阶段
    selected_candidate_id: Optional[str]   # 进入 SmartFix 的候选 ID
    fix_failed: bool                       # SmartFix 3 轮全失败
    fix_rounds_used: int                   # 实际使用的修复轮次
    last_error: Optional[str]              # 失败时的最后错误
    fix_history: List[Dict[str, Any]]      # 修复历史 [{round, sql, error}]
```

### DecisionResult 字段扩展（向后兼容）

```python
@dataclass
class DecisionResult:
    # 既有字段（保留）
    selected_sql: str = None
    selected_result: Any = None
    execution_time: float = None
    decision_reason: str = None
    voting_summary: Dict[str, Any] = None

    # 新增字段
    candidate_scores_r1: List[Dict] = field(default_factory=list)
    candidate_scores_r2: Optional[List[Dict]] = None
    selected_candidate_id: Optional[str] = None
    fix_failed: bool = False
    fix_rounds_used: int = 0
    last_error: Optional[str] = None
    decision_path: str = ""   # "A"/"B"/"C"/"D"/"E"/"F"/"G"/"H" 见路径表
```

---

## Execution Flow（最终冻结版）

### 主流程

```
CG → 5 候选 → ExecuteAll(无修复) → 分流
                                   ├─ 有成功 → 走"评分路径"
                                   └─ 全失败 → 走"逐个修复路径"
```

### 评分路径

```
剔除失败 → ScoreByData(R1) → 看 R1 最高分
                              ├─ 唯一=5 → 直接返回
                              ├─ 并列=5 → ScoreBySQL(R2)
                              │           ├─ 唯一最高 → 返回
                              │           └─ 并列 → 选最快返回
                              └─ <5 → 选最高分(并列选最快) → SmartFix
```

### 全失败路径

```
按错误等级取最轻一级（可能多个）
   ├─ 全是 TIMEOUT/RUNTIME/PERMISSION → 直接 fix_failed=True
   └─ 否则逐个 SmartFix
       ├─ 任一成功 → 立即返回
       └─ 全失败 → fix_failed=True + last_error
```

### SmartFix 子流程（每候选 ≤3 轮）

```
第 N 轮:
  LLM 修复(SQL, error, fix_history) → 新 SQL → 执行
    ├─ 成功 → 返回
    └─ 失败 → fix_history.append(...) → 进入下一轮
3 轮全失败 → 返回 fix_failed=True
```

### 收尾

```
所有路径 → result_verifier(结果可信度验证) → memory_update → END
```

---

## Path Enumeration（性能分析）

| 路径 | 触发条件 | LLM 调用数 |
|------|---------|-----------|
| A | R1 唯一=5 | 1 |
| B | R1 并列=5 → R2 唯一最高 | 2 |
| C | R1 并列=5 → R2 并列 → 选最快 | 2 |
| D | R1<5 → SmartFix 成功 | 1 + (1~3) |
| E | R1<5 → SmartFix 3 轮失败 | 4 |
| F | 全失败 → 逐个修，第 k 个第 m 轮成功 | 3(k-1)+m |
| G | 全失败 → 全部修不好 | 3×N (N=最轻候选数) |
| H | 全失败 → 最轻全是不可修 | 0 |

平均 1~4 次，最坏 ≤9 次（路径 G，假设 N=3）。

---

## Risks / Trade-offs

### 风险 1：R1 评分模型选错候选

**场景**：
```
c1 数据看着完美（R1=5），但 SQL 用错误字段巧合算对
c2 数据少一列（R1=4），但 SQL 是真正正确的
→ 选 c1 直接返回，错过 c2
```

**缓解措施**：
- 保留 `result_verifier`（结果可信度验证）作为末端兜底
- 评分输入包含元信息（行数、执行时间），让 LLM 能识别异常

**接受度**：这是评分模型能力问题，本次架构层面无法完全消除。

### 风险 2：3 轮修复仍不够

**场景**：原本要 2 候选各 1 轮才修对，现在只给 1 候选 3 轮。

**缓解措施**：
- 全失败分支可以尝试多个候选（最轻级别全部）
- `fix_history` 让每轮修复都有上下文，避免重复犯错

**接受度**：实测后若发现修复成功率低再迭代（可加"次优候选回退"）

### 风险 3：评分调用成为新瓶颈

**场景**：5 候选 × 20 行 × N 列数据可能产生 3000+ tokens 的 prompt。

**缓解措施**：
- cell 截断 20 字符
- 仅前 20 行
- 仅传成功候选

**接受度**：可接受，比修复 LLM 调用次数减少节省的时间多得多。

### 风险 4：中文思考会增加 token 消耗

**场景**：Qwen3 用中文思考可能比英文 token 消耗略多（中文每字 1~2 token，英文每词 1 token）。

**缓解措施**：
- 可通过环境变量关闭
- 业务价值（中文用户体验）大于 token 成本

**接受度**：可接受。

---

## SSE 事件流（API 新增）

```
data: {"type": "score_r1", "scores": [...]}
data: {"type": "score_r2", "scores": [...], "triggered_by": "r1_tie_at_5"}
data: {"type": "smart_fix_round", "round": 1, "sql": "...", "error": "..."}
data: {"type": "smart_fix_round", "round": 2, "sql": "...", "success": true}
data: {"type": "final_decision", "selected_id": "c1", "fix_failed": false}
```

前端可基于这些事件实时展示"评分→修复"过程。

---

## Testing Strategy

### Unit Tests

- `score_by_data()` —— 给定 mock 候选数据，验证返回评分格式
- `score_by_sql()` —— 给定 mock SQL + R1 评价，验证返回评分格式
- `pick_best_for_fix()` —— 给定 R1 评分，验证选择逻辑（最高/并列最快）
- `pick_lightest_failures()` —— 给定全失败候选，验证错误等级排序
- `smart_fix_with_history()` —— 验证 fix_history 在 prompt 中正确传递

### Integration Tests

- 路径 A (R1 唯一=5)：mock LLM 返回唯一最高分，验证直接返回
- 路径 B/C (R1 并列触发 R2)：验证 R2 触发条件和返回逻辑
- 路径 D (R1<5 + SmartFix 成功)：验证完整流程
- 路径 E (3 轮失败)：验证 fix_failed=True 返回
- 路径 F/G (全失败逐个修)：验证候选迭代和早退逻辑
- 路径 H (不可修)：验证直接返回不调 LLM

### End-to-End Tests

- 用 BIRD-SQL 数据集挑 10 个典型 query，对比新旧方案的：
  - LLM 调用次数
  - 端到端耗时
  - 最终 SQL 准确率

---

## Migration

由于 `NL2SQLState` 和 `DecisionResult` 都是向后兼容的字段扩展，本次变更无需数据迁移。

**部署顺序**：
1. 先合并 `LLMClient` 中文思考指令（影响最小，可独立回归）
2. 再合并 Execution + Decision 重构（核心变更）
3. 最后合并 SSE 事件流扩展（前端联调）

每一步都可独立 rollback。

---

## Open Questions

无（所有决策已在 explore 阶段冻结）。
