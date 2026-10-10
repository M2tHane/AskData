# ResultTable

结果表格：查询结果，带导出。

- `columns: [{key, label, field?, type?, masked?, digits?}]`：`field` 以等宽小字显示来源字段；`type="number"` 右对齐、等宽数字、千分位；`masked` 表示敏感字段已脱敏（掩码 + 眼睛划线图标）。
- `rows`、`total`、`ms`、`truncated`（达到行数上限时显示“已截断”警告，告诉用户结果不完整）。
- 页脚固定提供 CSV、Excel 导出。
- 表头 `surface-sunken`，行线 `line`，悬停 `surface-hover`。
