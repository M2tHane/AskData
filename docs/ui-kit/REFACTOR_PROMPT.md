# AskData 前端 UI 重构：给 Claude Code 的任务说明

> 用法：把整个 `askdata-ui-kit/` 目录放到仓库的 `docs/ui-kit/`，在仓库根目录启动 Claude Code，把本文件全文作为第一条消息发给它，或者对它说「按 docs/ui-kit/REFACTOR_PROMPT.md 执行」。

---

你要给 AskData 的前端（`frontend/`，React 18 + Vite + TanStack Query + zustand + 纯 CSS 变量）做一次 UI 重构。新的设计规范在 `docs/ui-kit/` 里。视觉参照 CC Switch v7：中性灰白为底、黑白做主操作、一抹品牌橙、系统字体栈；图表沿用 AskData 原有的配色和画法。

这次重构以视觉和交互为主，也包括少量**新增功能**。新增功能分成两类：后端已经支持、只差前端的，直接做；需要新接口的，先做界面并用功能开关隐藏，见「后端能力对照」。

## 一、先读这些，再动手

1. `docs/ui-kit/DESIGN.md`：设计规范总纲。**它和本文件冲突时，以本文件为准。**
2. `docs/ui-kit/tokens/tokens.json` 和 `tokens.css`：所有取值的唯一来源。
3. `docs/ui-kit/styles/components.css`：参考实现，类名前缀是 `ak-`。
4. `docs/ui-kit/components/*.md`：每个组件、每个整页的规则。`Page*.md` 就是各模块的页面需求说明，逐条落实。
5. 目标效果：
   - `docs/ui-kit/previews/*.html`：可以在浏览器里打开，可以交互，加 `#dark` 看深色。
   - `docs/ui-kit/screenshots/*-{light,dark}.png`：验收基准。
   - `docs/ui-kit/prototype/askdata-prototype.html`：整站原型，能切角色和深浅色，用来看页面之间怎么跳转。
6. `docs/ui-kit/styles/runtime.js`：预览里交互的参考写法（图表几何、分页、通知、可用字段面板）。**只参考行为**，在 React 里重新实现，不要把它搬进产品。
7. 现有代码：
   - `src/styles/{tokens,components,app}.css`
   - `src/components/*`，尤其是 `basic.tsx`、`charts.tsx`、`result.tsx`、`chat.tsx`、`reasoning.tsx`、`Toast.tsx`、`Pager.tsx`、`Select.tsx`、`Check.tsx`、`PromptEditor.tsx`、`UploadDialog.tsx`
   - `src/pages/*`、`src/lib/nav.ts`、`src/components/Icon.tsx`
   - `src/api/hooks.ts` 和 `schema.d.ts`

读完后，先用 10 行以内总结你的理解和执行计划，然后直接开始，不用等我确认。

## 二、硬约束

- **类名保留项目原有的 `cb-` 前缀。** 参考实现里的 `ak-` 只是设计稿用的：把规则移植到对应的 `cb-` 类上；没有对应旧类的新组件，用 `cb-` 新建。**绝对不要用 `ad-` 前缀**，广告拦截插件会把 `ad-` 开头的元素整个隐藏。
- **测试里断言了两个类名，必须保留：**
  - `cb-r`：`src/chat.test.tsx` 断言结果表数字单元格带这个类。类名保留，但它的样式改成左对齐，见下方「表格对齐」。
  - `cb-chart-plot`：`src/result.test.tsx` 用到。
  - 其他测试只能改选择器，不能删断言，也不能降低断言强度。
- **CSS 变量改用新 token 名。**
  - 先整体替换 `src/styles/tokens.css`。
  - 再引入 `docs/ui-kit/tokens/legacy-aliases.css`（拷到 `src/styles/`），让旧变量名在迁移期间还能用。
  - 全部迁完后删掉别名文件，并用 grep 确认没有旧变量名残留。
- **样式只引用 token 变量**，不写裸色值，不写裸 px 圆角。确实缺 token 时，先在 `tokens.json` / `tokens.css` 里补上并写清用途。
- **字体：** 去掉 `components.css` 第 1 行的 Geist Google Fonts 导入，`--font-sans` / `--font-mono` 改用 tokens 里的系统字体栈。正文字号从 14px 改成 13px。
- **不引入新的运行时依赖**（不加 Tailwind、组件库、图表库、图标库）。唯一例外：提示词的 YAML 校验和格式化可以加 `yaml` 这个包（eemeli/yaml）。加之前在报告里说明。
- **图标**继续用 `Icon.tsx`，缺的路径从 `docs/ui-kit/assets/icons.json` 复制：`pin`、`grid`、`more`、`user`、`settings` 等。
- **不改后端。** 需要新接口的功能按第四节处理。前端已有的 API 调用、状态管理、路由路径、权限裁剪逻辑不要改坏；新加的 UI 状态放在组件内部或现有 store 里。
- **无障碍：**
  - 焦点环统一用 `--focus`（2px，偏移 2px）。
  - 文本对比度 ≥4.5:1，控件边框和图标 ≥3:1，浅色和深色都要满足。
  - 只有图标的按钮必须写 `aria-label`。
  - Tab、分段控件、开关、下拉要有正确的 role 和 aria 状态。
  - Esc 关闭浮层。
- **每完成一个阶段**跑一次 `pnpm typecheck && pnpm lint && pnpm test`，全部通过后单独提交一个 commit，提交信息写清本阶段做了什么。

## 三、全局交互规则（每页都适用，细节见 DESIGN.md）

- **颜色：**
  - 主操作黑白：`inverse-bg` / `inverse-fg`。
  - 品牌橙 `action` 只用于品牌标记、开关打开、少量强调；橙色做文字时用 `action-text`。
  - 状态色成对使用：文字色 + 同名 `*-soft` 底。
- **表格对齐：** 所有表格、所有列一律**左对齐**，包括数字列。数字加 `font-variant-numeric: tabular-nums`。操作列放在最右。
- **列表卡片高度：**
  - 适用页面：数据源、语义、指标、反馈、查询日志、评测记录、团队与成员、提示词。
  - 列表卡片高度不随行数变化，统一撑到距窗口底边 120px 处，各页卡片底边对齐。
  - 内容多时在卡片内部滚动，表头吸顶，分页条贴在卡片底部。
- **分页：**
  - 列表超过 10 条时，卡片底部出现分页条：左边「共 N 条 · 每页 10 条」；右边是上一页、页码（超过 7 页时中间用省略号）、下一页、「跳至 [ ] 页」（回车跳转，输入框高 24px）。10 条以内不显示分页条。
  - 窄栏用紧凑版：上一页 · 1 / 3 · 下一页 · 跳至。
  - 在 `Pager.tsx` 上扩展，不要另写一个。
- **通知：**
  - 所有操作结果和后台事件，统一用右下角的小卡片（改造现有 `Toast.tsx`）：距右、下各 20px，宽 340px，最多同时 3 张。
  - 卡片结构：状态图标 + 标题 + 一行说明 + 可选的一个文字动作（「查看」「撤销」），底部 2px 进度条。
  - 默认 4 秒自动消失；带动作的给 6 秒；鼠标悬停暂停计时。
  - 容器用 `role="status"` + `aria-live="polite"`。
  - 删除、未保存离开这类需要用户做决定的事，用对话框，不用通知。
  - 规格见 `components/Toast.md`。
- **列表的四种非正常状态：** 首次为空、搜索无结果、加载中（骨架行，超过 300ms 才显示）、加载失败（写明原因 + 重试 + 错误编号），见 `components/ListStates.md`。每个列表都要接上。
- **未保存离开：** 语义、提示词、权限有未保存的修改时，切换对象或离开页面要先弹「有未保存的修改」，三个选项：放弃并离开 / 继续编辑 / 保存并离开。项目用的是 `<BrowserRouter>`，`useBlocker` 只在 data router 下可用：页面内切换对象用组件内拦截；离开路由的拦截，要么在侧栏导航点击时检查一个全局的「有未保存修改」标记，要么迁到 `createBrowserRouter`。迁路由算较大改动，先问我。浏览器关闭或刷新用 `beforeunload`。
- **宽度：**
  - 设计基准 1280px。
  - 1024–1279px 布局不变，表格靠省略号和换行压缩。
  - 1024px 以下侧栏收成 60px 图标栏，底部账号区也只显示图标并竖排，不能出现横向滚动。
  - 不支持手机。
- **复选框**不用浏览器原生样式：16px、圆角 4px、`line-control` 描边；勾选和半选用 `inverse-bg` 实底 + `inverse-fg` 细线勾 / 短横。
- **「新增」小标**只存在于设计稿，不要带进产品。
- **级别徽标：** 平台级灰、团队级蓝（info）、个人级橙（personal），全站同一套样式。数据里对应 `ScopeLevel` 的 `platform` / `team` / `personal`。

## 四、后端能力对照（据 `backend/openapi.json` 核对）

**已有接口，前端直接接：**

| 功能 | 接口 |
|---|---|
| 会话重命名 / 删除 | `PATCH /api/chat/conversations/{id}`，body 为 `{title}`；`DELETE /api/chat/conversations/{id}` |
| 新会话引导提问 | `GET /api/chat/suggested-questions` |
| 可用字段面板 | `GET /api/permissions/{ds}/scope`（当前用户可见的表和列），与 `GET /api/schema/{ds}/tables`、`GET /api/semantic?datasource_id=` 合并得到类型、别名、说明、枚举值和级别。先确认员工角色能否调用后两个接口；不能就只显示 scope 能给的部分，并在报告里提出 |
| 语义三级覆盖 | `/api/semantic`：`scope` 取 platform / team / personal，另有 `enum_mapping`、`owner_team_id`、`owner_user_id`。注意 `ScopeLevel` 在 openapi 里是空 schema，可以在 `src/lib/scope.ts` 手写类型 |
| 指标申请审核 | `GET/POST /api/promotion`，`POST /api/promotion/{id}/review` |
| 反馈转评测题 / 标记已处理 | `POST /api/feedback/{id}/convert`（`kind: eval`），`POST /api/feedback/{id}/handle` |
| 评测 | 新建、停止、续跑、对比：`/api/eval/runs*`、`/runs/compare`；加载基线题库：`POST /api/eval/cases/load` |
| 表结构同步结果 | `POST /api/schema/{ds}/sync`，然后轮询 `GET /api/schema/tasks/{task_id}`。结果 `SyncResult` 含新增 / 删除的表和列 |
| 上传历史 | `GET /api/upload/history/{ds}` |

**需要新接口：先做界面，用 `VITE_FEATURE_*` 开关默认隐藏，并在报告里写出建议的接口形状：**

| 功能 | 现状 | 开关 |
|---|---|---|
| 我的看板 + 回答上的「固定到看板」 | 后端完全没有 | `VITE_FEATURE_DASHBOARD` |
| 评测失败题「重跑这道题」 | 没有单题重跑接口。「去改语义」「去改提示词」两个跳转照常做 | `VITE_FEATURE_EVAL_RERUN_CASE` |
| 指标「近 30 天引用」列和按引用排序 | 没有引用计数 | `VITE_FEATURE_METRIC_REFS` |
| 字段「改名」检测、变化对语义和指标的影响分析 | `SyncResult` 只有新增和删除，改名会表现为一删一增。先按新增 / 删除展示；影响列能从已加载的语义和指标里匹配到就显示，匹配不到就不显示 | 不需要开关 |

**纯前端完成：**
- 提示词格式（Markdown / YAML）：后端没有格式字段，按内容自动识别。
- 提示词里必需变量的检查。

## 五、类名对照（`ak-` 参考 → 项目里的 `cb-`）

| 参考 | 项目 | 说明 |
|---|---|---|
| `ak-btn*` | `cb-btn*` | 高 32px；主按钮 `inverse-bg`；`-lg` 只在登录页用 |
| `ak-tabs` / `ak-tab` | `cb-tabs` / `cb-tab` | 胶囊式改为下划线式，`Tabs` 组件 API 不变 |
| `ak-seg` | `cb-seg` | 分段控件 |
| `ak-card*` | `cb-panel*` | 圆角 `radius-panel`，去掉阴影 |
| `ak-stat*` | `cb-stat` / `cb-grid-stats` | 标签在上，数字 24px |
| `ak-table` | `cb-data` / `cb-table` / `cb-runs` | 13px，行高 40px，表头 `text-3`，全部左对齐 |
| `ak-badge*` | `cb-badge*` / `cb-scope*` | 圆点 + 文字；级别徽标三色 |
| `ak-callout*` | `cb-callout*` | 整块浅底，没有左边色条 |
| `ak-toast*` | `cb-toast*` | 右下角卡片，见第三节 |
| `ak-field` / `ak-input` / `ak-cb` | `cb-field` / `cb-input` / `Check.tsx` | 描边 `line-control` |
| `ak-switch` | `cb-switch` | 打开时用 `action` |
| `ak-pager` / `ak-pg*` | `Pager.tsx` | 见第三节 |
| `ak-dd` / `ak-menu*` | `Select.tsx` / 新建 `cb-menu` | 下拉面板 `bg-card` + `border` + `shadow-md`，当前项 `bg-selected`；需要时带搜索框 |
| `ak-side*` / `ak-nav*` | `cb-side` / `cb-nav*` | 见阶段 3 |
| `ak-header` | 新建 `cb-header` | 见阶段 3 |
| `ak-q` / `ak-a` / `ak-think` / `ak-result*` / `ak-sql` / `ak-composer` | `chat.tsx` / `reasoning.tsx` / `result.tsx` 里对应的 `cb-` 类 | 见阶段 4 |
| `ak-dict*` | 新建 `cb-dict*` | 对话页的可用字段面板 |
| `ak-login*` | `cb-login*` | 见阶段 6 |

## 六、分阶段执行

**阶段 0：建立基线。**
- 用 `pnpm dev` 启动前端。需要后端时按 `deploy/` 下的 compose 文件起服务；起不来就用 Playwright 拦截 `/api/**` 返回假数据。
- 按管理员 / 团队负责人 / 员工三种角色，给每个路由截一张「改造前」的图，存到 `docs/ui-kit/before/`。
- 本阶段不提交 commit。

**阶段 1：token 与全局样式。**
- 替换 `tokens.css`，引入别名文件，去掉 Geist。
- 更新 `html, body` 的字号、字体和底色；全局焦点环改用新 token。
- 深色主题一起接上：`<html data-theme>` 取值「跟随系统 / 浅色 / 深色」，存到 `localStorage`（读写包在 try/catch 里）；跟随系统时监听 `prefers-color-scheme`。切换入口放在侧栏底部账号区（原型里是月亮图标）。
- 完成后整个应用应该已经换了颜色，布局还没变。

**阶段 2：基础组件。** 按对照表改造这些组件：
- `basic.tsx`、`Select`、`MultiSelect`、`Dialog`、`Check`、开关、`ScopeChips`；
- `Toast`（改成右下角卡片）、`Pager`（加页码和跳转）；
- 新建 `ListStates`（四种状态）和未保存离开的确认对话框。

对话框圆角用 `radius-dialog`，阴影用 `shadow-lg`。

**阶段 3：外壳。**
- `nav.ts`：
  - 给 `Route` 加 `placement: 'top' | 'bottom'`。对话、我的看板、数据源、语义与别名、指标放 `top`，保留「查数 / 数据」分组名；其余放 `bottom`，不分组，平铺沉底。顺序见 `previews/Sidebar.html`。
  - 新增「我的看板」路由（`/dashboard`，三种角色都有），受 `VITE_FEATURE_DASHBOARD` 控制。
  - 员工没有任何 `bottom` 入口时，这一段整个不渲染。
- 侧栏：
  - 宽 216px，底色 `bg-sidebar`。
  - 顶部是品牌标记（22px 橙色方块里一个白色「A」+「AskData」）。
  - 底部是账号行：头像 + 名字 + 角色 + 深浅色切换 + 退出。
  - **导航项后面不显示任何数字**，反馈和用量都不显示。
  - 窄于 1024px 时收成图标栏。
- 新建 `PageHeader`（`cb-header`，高 56px）：图标 + 页标题 + 右侧操作区。对话页不套，其余页面都套上；把各页散落的「刷新、导出、新建」按钮移进操作区。
- `index.html` 的 `<title>` 改成「AskData」。

**阶段 4：对话页**（以 `components/PageChat.md`、`ChatTurn.md`、`ResultCard.md`、`ChatStates.md`、`ChatEmpty.md` 和对应预览为准）。
- 三栏到顶，没有页头：会话列表 / 对话流 / 可折叠的可用字段面板。
- **左右交替：**
  - 提问是右对齐的 `inverse-bg` 气泡，最宽 70%。
  - 回答靠左，**不加头像**，最宽到对话流宽度减 64px，右侧始终留白。
  - 提问和回答的字号、行高、字重一致：15px / 24px / 400。
- **一轮回答的信息层级：**
  - 顶部是一行可折叠的灰字「已思考 N 秒」。
  - 接着依次是：结论 → 图表（图 / 表切换和多种图型，沿用现有 `ResultCard` 的能力）→ 底部一行。
  - 底部一行：左边是「口径 N 条」「SQL 只读 · 已校验」两个折叠项，同一时间只展开一个；右边是「固定到看板」（受看板开关控制）、「有用」、「没用」、「复制」。
- **图表：**
  - 沿用 `charts.tsx` 的几何：柱宽每个系列最大 28px，堆叠柱最宽 48px，折线 2px，实心饼图且图例在右侧，散点 4.5px。
  - 配色用 `chart-1`…`chart-5`；坐标轴和数值文字 12px，字重 500 / 600。
  - **必须按容器的实际宽度绘制**，并用 `ResizeObserver` 在宽度变化时重绘。不能先按固定宽度画、再靠 viewBox 缩放：图表在隐藏状态或布局完成前首次绘制时，读到的宽度是 0，回退到固定宽度后会被放大，文字变得特别大。设计稿里实际出现过这个问题。
- **会话列表：**
  - 按「今天 / 更早」分组，超过 10 条时底部用紧凑分页。
  - 每行悬停或当前行时显示「⋯」，菜单里是「重命名」（对话框里只有名称输入框，没有说明小字）和「删除」（二次确认）。
  - 顶部条**只放会话标题**，没有重命名和导出按钮。
- **可用字段面板**（右侧 320px）：
  - 「可用字段」按钮固定在对话区右上角，面板展开和收起时位置不变；面板本身没有标题和收起按钮。
  - 进入对话页或新建会话时默认展开；用户一发出消息（点发送或按回车）就自动收起，之后可以随时再打开。
  - 窄于 1440px 时，面板展开期间会话列表暂时让位。
  - 顶部是表的下拉：下拉里先是搜索框，可以按表名或中文名过滤；每个选项三列，表名靠左，中文名和字段数靠右，字段数列按「999 个字段」预留宽度。只列当前用户可见的表。
  - 中间是字段列表：字段名 / 类型 / 别名，每页最多 15 行，超出时用紧凑分页，翻页后自动选中该页第一行。默认选中第一张表的第一个字段。
  - 点一行，面板底部显示：`表.字段`、级别徽标（平台级 / 团队级 / 个人级，**不要写「来自 XX」**）、说明、枚举值含义，以及「插入到问题」（把别名追加到输入框）。
- **新会话**：用 `ChatEmpty`，6 个来自 `/api/chat/suggested-questions` 的示例问题，两列卡片；点一下填进输入框，不直接发送。

**阶段 5：管理页逐页改造。** 每页以 `components/Page*.md` 的说明和截图为准。下面只列容易做错的点：
- **数据源：**
  - 每行的操作按固定槽位排：数据库行是「测试 / 编辑 / 同步」，上传文件行只有「上传数据」，占「同步」那一格；开关永远在最后一格。导入中的开关照常显示，但不可点。
  - 「新建数据源」先弹类型卡片：卡片等大、三列、带数据库图标（`public/datasource-icons`）。GaussDB 卡片只写「GaussDB」。
  - Excel / CSV 只能通过上传进来。
  - 同步后发现变化时，状态列显示「字段变化 N」，点开右侧抽屉看明细，抽屉里有跳转和「标记已处理」。
- **语义与别名：**
  - 左边是表列表，右边是字段表，两张卡片高度固定。
  - 表头压成一行：表名、别名、说明。
  - 字段表的列：字段（下面一行小字是类型 · 来源）、别名、说明、枚举值映射。勾选「枚举」后在该字段下方展开「值 → 含义」列表。
  - **三级覆盖：**
    - 平台是默认，只有管理员能改。
    - 团队负责人覆盖平台，对本团队生效。
    - 员工覆盖团队和平台，只对自己生效。
    - **编辑哪一级由角色决定，页面上不放层级切换。**
    - 来源显示「沿用团队 / 沿用平台」，或本层的徽标加「恢复为团队 / 恢复为平台」。恢复就是删掉本层的覆盖，回到上一级当前的值。
  - 不要「申请提升」。
  - 有字段变化的表在列表上标「N 处变化」。
  - 有未保存修改时切换表要先确认。
- **指标：**
  - 分「指标」和「申请」两个 Tab。
  - 指标表**不做版本列**。
  - 「近 30 天引用」列受开关控制。
  - 引用的字段被删除时，校验列显示「引用字段已删除」。
- **反馈：** 接上「转为评测题」和「标记已处理」，操作结果用通知反馈。
- **查询日志：** 统计卡、筛选、抽屉（含各环节耗时）照稿子做。耗时数据拿不到就不显示那一块。
- **评测：**
  - 四个 Tab：概览（统计卡 + 通过率趋势）/ 评测记录（「新建评测」在这里）/ 题库（「加载基线题库」「运行全部」在这里）/ 两次对比。
  - 两次对比里的失败题带失败原因，并有「去改语义」「去改提示词」「重跑这道题」三个操作，最后一个受开关控制。
- **团队与成员：**
  - 团队行只有「管理」一个入口，改名、选负责人、添加成员、设为负责人、移出团队都在同一个对话框里，按钮尺寸一致。
  - 「添加用户」和「编辑用户」是两个不同的对话框：编辑时用户名和团队只读，重置密码也在编辑里。
- **模型配置：**
  - 页面**只保留模型列表**，删掉「团队默认模型」卡片。
  - 同步修改 `ModelsPage.tsx` 第 62 行那条 CONFLICT 错误文案，它现在让用户去「团队默认模型」里换，入口没了就要改。
  - 团队默认模型的后端能力不要动，在报告里提醒我它目前没有界面入口。
  - 表格不出现横向滚动，接口地址列固定宽度、超长省略、悬停看全文；服务商放在模型名下面一行。
- **提示词：**
  - 不显示「修改会立即影响…」的警示。
  - 左侧「提示词」「历史版本」两张卡都可以折叠。
  - 编辑器按内容自动识别 Markdown 或 YAML，并分别高亮：
    - Markdown 高亮标题、列表、粗体、行内代码；
    - YAML 高亮键、字符串、数字、注释；
    - 两者都高亮 `${变量}` 和 `<untrusted_data>` 块。
  - 底部状态栏显示格式校验结果（YAML 解析错误要指出行号并禁用保存）和必需变量检查。
  - 「格式化」按钮按各自格式整理。
- **用量与审计：** 不放「哪个组超限了」的提醒。
- **权限：** 照稿子做；三态复选框用新的复选框样式。
- **我的看板**（受开关控制）：
  - 两列卡片，每张是原问题 + 数据源与刷新时间 + 图表或单值大数字，右上角是刷新、在对话中打开、取消固定（通知里给「撤销」）。
  - 卡片有加载中和刷新失败两种状态。

**阶段 6：登录页。** 以 `previews/LoginPage.html`、`LoginStates.html` 为准：
- 布局：单页双栏，左边表单，右边是静态示意（`aria-hidden`）。窄于 880px 时只保留表单。
- 输入框：账号和密码放在同一页；加 `autocomplete="username"` / `current-password`；账号框自动聚焦。
- 密码框：右侧有「显示 / 隐藏」（带 `aria-pressed`）；用 `getModifierState('CapsLock')` 检测并提示大写锁定。
- 「记住账号」：只存用户名，存进 `localStorage`（读写包 try/catch），**不存密码**。
- 「忘记密码？」不跳转，只在主按钮下方显示「请联系你的团队负责人或管理员」。
- 错误提示：
  - `AUTH_FAILED` 统一显示「账号或密码不正确」，不暴露账号是否存在；
  - `RATE_LIMITED` 用 warning 提示框；
  - 其他错误沿用 `lib/errors.ts`。
  - 错误显示在主按钮上方，同时把焦点移回密码框。
- 提交时按钮禁用并显示「登录中…」。
- 保持原有的 `login` → `setTokens` → `fetchMe` → `setUser` 流程不变。

**阶段 7：清理与验收。**
- 删除 `legacy-aliases.css`，grep 确认没有旧变量名残留；删除不再使用的 CSS。
- `pnpm typecheck && pnpm lint && pnpm test && pnpm build` 全部通过。
- 用 Playwright 按三种角色、浅色和深色，在 1280×880 给每个路由截「改造后」的图，存到 `docs/ui-kit/after/`。逐张和 `docs/ui-kit/screenshots/` 对比，列出差异并修掉。另外在 1100px 和 1000px 宽度各抽查一遍，确认没有横向滚动、列没有被挤没。
- 用键盘走一遍：登录、提问、切 Tab、打开下拉并搜索、翻页并跳页、关闭对话框，确认焦点环可见、Esc 能关浮层。
- 最后给我一份简短报告：
  - 改了哪些文件，每个阶段的 commit；
  - 截图对比中保留的差异和原因；
  - 被开关隐藏的功能，以及建议的后端接口形状；
  - 你拿不准、需要我决定的点。

## 七、遇到这些情况先停下来问我

- 某个改动必须改后端、或者改坏现有 API 调用才能实现。
- 规范和现有功能冲突，比如某页的信息放不进标准骨架。
- 要删除现有功能、入口或测试断言。规范里明确要删的不用问：模型页的「团队默认模型」卡片、侧栏数字、对话顶部条的重命名 / 导出按钮、语义页的「申请提升」、指标的版本列、用量页的超限提醒。
- 想加 `yaml` 以外的新依赖。
