统计卡：一行 3–4 张，`ak-stats` 自适应网格（最小 160px，间距 `space-3`）。照 CC Switch 的 `UsageHero`：高 `stat-height`（76px），内边距 12px × `space-3-5`。

- 标签 `caption` + `text-2`，可带 13px 问号说明口径；数值 `metric`（24px/600，tabular-nums，`text-1`）。
- 第五个以后的指标收进右侧「更多指标」，不加第二行卡片。
- 变化量放在 title 提示或「更多指标」里；若必须外露，用 `ak-stat-delta`，上升 `success`、下降 `danger`，文字里写「+」「−」。
- 数字用缩写（1.24B、3,930），完整值放在 title 提示里。
