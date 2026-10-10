---
name: chatbi-design-system
description: ChatBI 查数平台的设计系统（颜色/字体/间距 tokens、20 个组件规范、对话页与日志页样例）。在为 ChatBI 写或改任何前端界面、组件、样式、页面布局、文案时使用。
---

# ChatBI 设计系统

为 ChatBI 写前端时，按下面的顺序参考本目录，不要自行发明颜色、字号、间距或组件样式。

## 先读什么

1. `README.md` —— 设计规范：原则、文案语气、颜色/字体/间距/布局规则、图标。**每次开始界面工作前先读。**
2. `tokens.json` —— 全部设计 token 的唯一来源（含浅色/深色两套值和每个 token 的用途说明）。
3. `tokens.css` —— 由 tokens.json 生成的 CSS 变量（`--canvas`、`--ink`、`--space-4`、`--radius-md`、`--font-sans` …）以及字号类（`.body`、`.heading`、`.code` …）。深色主题：在根元素上设 `data-theme="dark"`。
4. `components/<组件名>/README.md` —— 每个组件的用途、props、该做与不该做。用某个组件前先读它。
5. `components/index.d.ts` —— 组件 props 类型。
6. `components/bundle.css`、`components/bundle.js` —— 参考实现（`.cb-*` 类名 + 无构建的 React 18 实现，挂在 `window.ChatBI`）。
7. `components/<组件名>/preview.html`、`components/ChatWorkspace/preview.html`、`components/QueryLog/preview.html` —— 可直接在浏览器打开的示例；`screenshots/` 是整页截图。

## 实现规则

- 所有颜色、间距、圆角、阴影、字体都用 `tokens.css` 里的 CSS 变量，不写死十六进制值或像素值（1px 描边除外）。
- 如果项目用 Tailwind：在 `tailwind.config` 的 `theme.extend` 里把颜色、spacing、borderRadius、fontFamily 映射到这些 CSS 变量，而不是用 Tailwind 默认色板。
- 在项目里用 React/TypeScript 重写组件时，保持 `index.d.ts` 的 props 形状和 `bundle.css` 的视觉；`bundle.js` 是参考实现，不要求原样引入。
- 状态（成功、拦截、警告、澄清）必须同时有图标和文字；图表二分类用 `chart-1` 蓝对 `chart-4` 橙。
- 一轮回答的固定顺序：思考过程（ReasoningPanel，输出结论后自动折叠）→ 结论句 → ResultCard（图表/表格切换）→ SQL → FeedbackBar。对话里不再显示 ExplainCard。
- 语义与指标出现的地方都带 ScopeTag（平台级 / 团队级 / 个人级）。
- 没有权限的入口直接不渲染，不做置灰。
- 界面文案用简体中文，动词开头的按钮文案，不用 emoji 和感叹号。
- 新增组件或 token 时，同时更新 `tokens.json`/`tokens.css` 和对应 README，保持本目录是唯一来源。
