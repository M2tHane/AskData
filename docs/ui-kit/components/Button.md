按钮：一页最多一个 `ak-btn-primary`；主操作是黑白的（`inverse-bg` / `inverse-fg`），不用品牌橙。

- 使用方提供：文字（动词开头，2–4 个字，如「立即同步」「新建数据源」），可选的前置 16px 图标。
- 尺寸：默认 `control-md`（32px）、圆角 `radius-control`；表格行内 `ak-btn-sm`（`row-sm` 28px）；登录页 `ak-btn-lg ak-btn-block`（`control-lg` 40px）。
- `ak-btn-secondary`：`bg-card` + `border-strong` 描边，页头操作用它（截图里的「立即同步」）。
- `ak-btn-ghost`：`text-2`，卡片头、SQL 块里的次要操作。
- `ak-btn-danger`：`danger` 字 + 普通描边，只用于停用、删除，且要二次确认。
- 禁用一律 `opacity: .45`。只有图标时加 `ak-btn-icon` 并写 `aria-label`。
