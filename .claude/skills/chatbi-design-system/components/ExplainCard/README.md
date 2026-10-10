# ExplainCard

结果解释卡：“这样理解你的问题”，回答里必须出现（PRD 1.3 P0）。

- `summary`：一句话复述系统的理解。
- `metric: {name, scope, expr}`：用了哪个指标、生效的是哪一级（ScopeTag）、展开后的表达式。
- `terms: [{term, field, scope}]`：命中的别名/同义词/枚举映射及其级别。
- `filters`、`timeRange`：过滤条件和把相对时间换算成的绝对区间。
- 用 `info-soft` 底，字段和表达式用等宽内联代码。
