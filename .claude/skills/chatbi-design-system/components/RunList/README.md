# RunList

查询日志列表：每次提问一行，含被拦截的请求（PRD 1.2）。

- `rows: [{status: 'ok' | 'blocked' | 'error' | 'fixed' | 'clarify', question, reason?, detail?, user, id, time}]`，`showUser`（员工看自己的历史时设为 false）。
- 被拦截的行把原因代码和人话原因写在问题下方，`danger` 色。
- 只展示 SQL 和结果摘要，不存完整结果；行尾“查看”打开详情（SQL、Trace、模型、token）。
- 管理员看全平台、负责人看本团队、员工只看自己的会话。
