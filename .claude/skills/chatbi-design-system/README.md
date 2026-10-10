ChatBI 是企业内部的自然语言查数平台：业务人员用一句话查数、出图，平台保证越权不可能、SQL 只读、口径可控、质量可评测。界面是一个**工作台**，不是营销页：灰底上的白色面板、黑色主操作、细线分隔，颜色只留给状态和数据。

## 设计原则

1. **结论先行，过程可查。** 回答第一句给数字和对象；口径、SQL、校验、过程都在，但收在 ExplainCard 和右侧检查面板里，用户想看时一步可达。
2. **每个数字都说得清来源。** 用了哪个指标、哪条别名、哪一级（平台 / 团队 / 个人）生效、时间换算成了什么区间——必须出现在 ExplainCard 里，并用 ScopeTag 标级别。
3. **安全是可见的。** 只读、单条 SELECT、可见范围、拦截原因不藏在日志里：SQL 头部固定显示“只读 · 已校验”，被拦截时用 Callout 写出原因代码。
4. **看不到就是不存在。** 没有权限的入口、表、字段不显示、不置灰；提示里只说“你看不到 X，联系谁”，不暴露其他团队的数据结构。
5. **歧义先问，不猜。** 有两种口径时用 ClarifyPrompt 让用户选，选择可记为个人级别名。

## 内容与语气

- 中文为主，界面用简体中文；表名、字段名、SQL、run id、原因代码保持英文原样，用等宽字体。
- 称呼用户为“你”，系统不自称“我”，回答直接陈述事实：「9 月华东区 5 位销售共回款 406.5 万元」，不写「根据查询结果显示……」。
- 按钮用动词开头：「执行查询」「转为评测样例」「同步元数据」，不用「确定」「OK」。
- 错误与拦截写清三件事：发生了什么、为什么（原因代码）、下一步：「查询已拦截：包含 2 条语句。只允许单条 SELECT。原因：multi_statement」。
- 数字：千分位（1,284,300）；金额超过 1 万可用“万”，保留 1 位；时间用 `今天 14:22`、`昨天`、`周一`，超过一周用 `2026-09-30`。
- 不用 emoji，不用感叹号。

## 颜色

- 层级：`canvas`（最外层灰底）→ `surface`（面板、卡片）→ `surface-sunken`（代码块、表头、内凹区）→ `surface-hover`（悬停）。面板之间靠 `line` 描边区分，不靠阴影。
- 文字：正文 `ink`，次要信息 `ink-muted`；两者在所有 surface 上 ≥4.5:1（两个主题都已核对）。
- 主操作：`primary` 黑底 + `on-primary`。每个视图最多一个主按钮；选中的胶囊 Tab、用户提问气泡也用 `primary`。
- 品牌色 `accent`（朱红）只用于：侧栏选中项（`accent-soft` 底 + `accent` 图标）、行内链接、个人级 ScopeTag。不用于报错。
- 状态：`success` 通过 / 成功，`danger` 拦截 / 失败，`warning` 需处理但未阻断（澄清、纠错重试、截断），`info` 说明（结果解释、团队级）。每个状态都带 `-soft` 底色版本。**状态必须同时有图标和文字**，不只靠颜色区分。
- 三级语义：`scope-platform`（灰，方形标记）、`scope-team`（蓝，菱形标记）、`scope-personal`（朱红，圆形标记）。
- 图表：系列依次 `chart-1` … `chart-5`，对 `surface` 都 ≥3:1；二分类（成功 / 失败、本期 / 上期）用 `chart-1` 蓝对 `chart-4` 橙，不用红绿。网格线 `chart-grid`。
- SQL 高亮：`code-keyword`、`code-string`、`code-number`、`code-comment`。
- 焦点环：2px 实线 `focus` + 2px 偏移，对所有 surface ≥3:1。

## 字体

- 界面用 `sans`（Geist，中文回落 PingFang SC / 微软雅黑 / Noto Sans SC）；SQL、表名、字段、run id、原因代码用 `mono`（Geist Mono）。两者经 Google Fonts 加载，`components/bundle.css` 顶部已 `@import`。
- 字号阶梯：`display` 28 只用于空状态；页面标题 `title` 20；面板标题 `heading` 15；正文 `body` 14、强调 `body-strong`；元信息 `small` 13；分组标题 `eyebrow` 12（`ink-muted`，英文全大写，字距 0.06em）；徽标 `caption` 12；代码 `code` 13、内联标识符 `code-sm` 12。
- 表格和统计数字一律等宽数字（`font-variant-numeric: tabular-nums`，类名 `.cb-num`），数值列右对齐。

## 间距、圆角、尺寸

- 4px 基准：`space-1` 4 … `space-10` 40。面板内边距 `space-5`，卡片内边距 `space-4`，面板之间 `space-4`，对话消息之间 `space-6`。
- 圆角：`radius-sm` 6（徽标、ScopeTag）、`radius-md` 8（按钮、输入框、表格、气泡、代码块）、`radius-lg` 14（大面板、Composer）、`radius-pill`（胶囊 Tab、Chip）。
- 控件高度：`control-md` 36（默认）、`control-sm` 28（面板头部、表格行内）。
- 阴影：`shadow-panel` 几乎不可见，只给面板一点浮起；`shadow-pop` 只给浮层（下拉、点踩原因弹层）。

## 布局

- 应用外壳：左侧 Sidebar（`sidebar-width` 232）+ 顶栏（60px，面包屑 / 标题 / 数据源徽标 / 右侧操作）+ `canvas` 上的面板网格。
- 对话页三栏：会话列表（约 200）｜对话（弹性，正文最大 `chat-max-width`）｜检查面板（`inspector-width` 380，Tab：SQL / 校验 / 过程 / 解释）。窄屏时检查面板折叠为抽屉，会话列表收进侧栏。
- 一轮回答的固定顺序：思考过程（ReasoningPanel，输出结论后折叠）→ 结论句 → ResultCard（图表/表格切换）→ SQL → FeedbackBar。对话里不再显示 ExplainCard。
- 管理页（数据源、语义、指标、权限、日志、评测）：顶部统计面板一行，下方一个主面板（Tabs 筛选 + 列表）。
- 导航按角色裁剪：员工看到「查数」和数据下的「语义与别名」「指标」（个人级）；团队负责人加「反馈」「查询日志」「评测（只读）」；管理员看到全部。

## 状态与动效

- 悬停：`surface-hover` 底；主按钮悬停降低不透明度。禁用：45% 不透明度，不可点。
- 流式输出：末尾闪烁光标；Trace 逐步追加，进行中的一步图标旋转。除此之外不做动画。
- 空状态：一行 `display` 标题 + 一句说明 + 3 个当前数据源下可用的示例问题（ClarifyPrompt 的 Chip 样式）。

## 图标

- 自带线性图标集（`Icon`）：24 单位网格、1.6 描边、圆角端点、`currentColor`，默认 16px，徽标内 14px。
- 状态图标固定：`check` 通过，`alert` 警告 / 失败，`shield` 拦截与安全，`x` 失败步骤，`refresh` 纠错重试，`help` 澄清，`eyeOff` 脱敏，`lock` 绑定 / 只读。
- 纯图标按钮必须有 `aria-label`。

## 标识

- 目前没有正式 Logo：侧栏用黑底圆角方块内的等宽 “BI” 字样 + “ChatBI” 文字。拿到正式 Logo 后放进 `assets/Logos/` 替换。
