# Callout

提示条：一段需要用户读到的说明，嵌在内容里，不浮动。

- `tone`：`danger`（拦截、越权、失败，图标为盾牌）、`warning`（纠错重试中、即将截断）、`info`（权限范围说明）、`success`（试跑通过）。
- `title` 一句话说结果，正文说原因和下一步；`actions` 放行内链接。
- 拦截类提示必须写出原因代码（如 `multi_statement`、`system_table`、`forbidden_function`），与查询日志一致。
- 权限相关只说“你看不到 X / 联系谁”，不暴露其他团队的表名或字段。
