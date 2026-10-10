# Switch

启用 / 停用开关，替代列表里的“状态”列。

- `checked`、`onChange(next)`、`label`（对象名，读屏念作「停用销售运营部」）、`disabled`。
- 绿色（`success`）= 启用，灰色（`line-control`）= 停用；`role="switch"` + `aria-checked`。
- 放在行内操作的最后（「编辑」之后），和按钮一起包在 `.cb-row-actions` 里。停用的行加 `.cb-row-off`，文字变灰，操作仍可用。
- 只用于真正的启停。生命周期状态（待审核、生效中、配额告警）不做成开关：待审核挂在级别旁，生效中挂在版本号旁，配额等级写在使用率后面。
- 被引用而不能停用时（如团队正在用的模型），后端返回 CONFLICT，用 toast 说明原因。
