# AskData 项目要点

验证和实现时用到的项目事实。动手前确认它们仍然成立（路径、命令可能变）。

## 角色与账号

- 角色：`admin`（管理员，无团队）、`leader`（团队负责人）、`member`（员工）。后端 `app/core/deps.py` 的 `Role`。
- 导航按角色裁剪：`frontend/src/lib/nav.ts` 的 `ROUTES`；没权限的入口直接不渲染。
- 演示账号（含密码）：`deploy/.env.demo-accounts`（JSON）。每个角色至少有一个账号；验证时逐角色登录。不要把密码写进代码、文档或回复。

## 运行环境

- 后端在 Docker 中运行（项目名 `askdata`）：api `127.0.0.1:8000`、worker、pg-meta、pg-import（业务/评测库）、redis、minio。
- **后端代码和 `eval/` 打包在镜像里**，改了必须重建：

  ```bash
  docker compose -p askdata --env-file deploy/.env -f deploy/docker-compose.yml \
    -f deploy/docker-compose.local.yml build api
  docker compose -p askdata --env-file deploy/.env -f deploy/docker-compose.yml \
    -f deploy/docker-compose.local.yml up -d --wait --wait-timeout 180 migrate api worker
  ```

  （zsh 下不要把命令放进变量再展开，会被当成一个词。）
- 前端用 Vite 开发服务器 `http://127.0.0.1:5173`，热更新，`/api` 代理到 8000。
- 只读查评测业务库：`docker exec -i askdata-pg-import-1 psql -U import_admin -d askdata_import`，先 `SET default_transaction_read_only=on`；评测 schema 为 `eval_commerce`。

## 实现约定

- 后端模块结构：`backend/app/modules/<模块>/{api,service,repository,models,schemas}.py`；接口返回 `Envelope`；写接口用 `require_role` 检查角色，service 内再 `_role` 校验；写操作调用 `AuditService.record`。
- 数据库会话 `expire_on_commit=False`：加锁重读状态时要 `execution_options(populate_existing=True)`，否则读到旧值。
- 后台任务：`app/core/tasks.py` 的 `TaskManager`（arq）。长任务要考虑：停止、继续、worker 重启后的恢复、旧 worker 晚到的写入（用 task_id 归属判断）。
- 数据库变更：`backend/migrations/versions/` 新增 migration；评测/上传相关的测试夹具 `tests/test_upload_d.py::d_database` 只跑指定的 migration，新增的要加进去。
- 接口类型重新生成：

  ```bash
  cd backend && uv run --frozen python -c "import json; from app.main import create_app; \
    open('openapi.json','w').write(json.dumps(create_app().openapi(), ensure_ascii=False, indent=1, sort_keys=True)+'\n')"
  cd ../frontend && pnpm -s gen:api
  ```

- 前端：先读 `docs/ui-kit/DESIGN.md`；组件在 `frontend/src/components/`（`basic.tsx` 的 Button/Badge/Tabs/Segmented/Panel/TextField/Callout/ScopeTag，`Dialog`/`Drawer`/`ConfirmDialog`/`UnsavedDialog`、`Select`、`MultiSelect`、`Pager`、`ListStates`、`useToast`）；样式只用 `styles/tokens.css` 的变量，页面样式写在 `frontend/src/styles/pages/<页面>.css`；时长用 `lib/format.ts` 的 `formatSpan`/`formatMs`。
- 进行中的数据用 react-query `refetchInterval` 轮询，结束后返回 `false` 停止。

## 检查命令

```bash
# 后端（backend/ 下）
uv run --frozen ruff check app tests migrations && uv run --frozen ruff format --check app tests migrations
uv run --frozen mypy --config-file pyproject.toml app
uv run --frozen pytest -c pyproject.toml -q tests
# 前端（frontend/ 下）
npx tsc -b --noEmit && npx eslint src && npx vitest run
# 评测资产（仓库根目录）
uv run --frozen --project eval python eval/validate_cases.py --postgres docker
```

## 浏览器验证

- 用 Playwright MCP：`browser_navigate` → 登录 → `browser_snapshot` 找元素 → 操作 → `browser_take_screenshot`（文件存到 `.playwright-mcp/` 下）→ 用 Read 看截图。
- 每个状态都截图看：空、加载、进行中、部分完成、失败、完成、数据多。
- 验证中产生的数据（测试运行、测试记录）结束时删除，并在报告里说明。
