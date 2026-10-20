分段控件：同一份数据的几个互斥视角，例如趋势图的「提问 / Token / 成本」、筛选行的团队切换。

- 轨道 `bg-subtle`、圆角 `radius-panel`、内边距 3px；选项高 `control-xs`（26px）、12px/500；选中块 `bg-card` + `shadow-sm` + 圆角 `radius-seg-item`。
- 未选 `text-2`，选中 `text-1`。选项 2–6 个，每个不超过 6 个字，多了改用 `Dropdown`。
- 每个按钮写 `aria-pressed`，外层 `role="group"` + `aria-label`。
- 换「视角」用它，换「内容」用 `Tabs`。
