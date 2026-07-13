# E2E 测试（change clarify-choice-inspector-cancel）

基于 Playwright 的端到端测试，覆盖三个新行为：

| 文件 | 场景 |
|---|---|
| `clarify-choice.spec.ts` | 缓存命中反问展示二选一按钮（是/否），点"是"复用、点"否"重新生成 |
| `inspector-cross-turn.spec.ts` | 检查器跨轮锁定：点旧轮节点 pin -> 新轮不切换 -> "返回最新"切回 |
| `request-cancel.spec.ts` | 在途请求点"停止" -> Turn 显示"已取消"，无"推理进行中" |

## 前置条件

1. **后端运行**：`python run_api.py`（监听 :8000），Vite proxy `/api/v1` -> :8000。
2. **数据库可用**：AppLayout 启动时会自动选中第一个数据库；确保后端 `listDatabases` 返回非空。
3. **clarify-choice 场景**需可命中的历史相似查询：测试会先发一次查询建立会话历史，再发相似查询触发 `cache_confirm`。若 cache 未命中，该用例自动 skip。

## 安装

```bash
cd frontend
npm install                 # 安装 @playwright/test（已加入 devDependencies）
npx playwright install      # 下载 Chromium 浏览器（首次约 150MB）
```

## 运行

确保后端已起，然后：

```bash
cd frontend
npx playwright test         # 跑全部 E2E
npx playwright test request-cancel.spec.ts     # 单个
npx playwright test --headed                  # 可视化
npx playwright test --ui                       # Playwright UI 面板
```

`playwright.config.ts` 会自动起前端 dev server（`npm run dev`，:5173）；后端需手动起。

## 用 Claude Code Playwright 插件运行

按 CLAUDE.md #9，开发完成后可用 Playwright 插件交互式完成测试：在插件中打开上述 spec，逐场景执行并观察。三个 spec 的断言点：

- clarify-choice：`getByRole('button', { name: '是，复用' })` 可见、输入框隐藏、点击后出结果。
- inspector-cross-turn：`getByText(/已锁定到第 1 轮/)` 可见 -> 点"返回最新" -> 该提示隐藏。
- request-cancel：点"停止" -> `getByText('已取消')` 可见 -> `getByText('推理进行中…')` 隐藏 -> "发送"按钮恢复。

## 已知限制

- **clarify-choice** 依赖 cache 命中，非确定性；未命中时自动 skip。
- **request-cancel** 需查询足够慢；查询过快结束时自动 skip。
- **inspector-cross-turn** 依赖两轮查询均能发起；节点选择用中文标签定位，若 UI 文案变动需同步。
