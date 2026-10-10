# ChartCard

图表卡：按结果自动选图，用户可在柱状 / 折线之间切换（PRD 1.3 P0）。

- `title`（带单位）、`data: [{label, values: number[]}]`、`series: string[]`（≤5）、`type: 'bar' | 'line'`、`format(v)`、`caption`。
- 自动选图规则：时间维度 → 折线；类别维度 ≤20 个 → 柱状；单值 → 不出图，只出数字；其余 → 只给表格。
- 系列依次用 `chart-1` … `chart-5`；成功/失败这类二分用 `chart-1`（蓝）对 `chart-4`（橙），不用红绿。
- 网格线 `chart-grid`，坐标文字 `ink-muted`；悬停显示精确值。
