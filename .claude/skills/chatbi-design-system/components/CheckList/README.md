# CheckList

校验清单：SQL 执行前的安全校验结果（PRD 1.3 SQL 安全 P0）。

- `items: [{status: 'pass' | 'fail' | 'warn', label, detail?}]`。
- 固定项：只读账号、单条 SELECT、可见范围内的表和字段、禁用函数与系统表、超时、行数上限。
- `fail` 整行 `danger-soft` 底；`warn` 只图标变色。每项都有图标 + 文字，不只靠颜色。
- 放在右侧检查面板的“校验”Tab，也用于指标保存前试跑、管理员发布模型配置前。
