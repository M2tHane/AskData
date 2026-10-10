# ChatMessage

一轮对话消息：用户提问是右侧黑底气泡，系统回答是左侧无底色的内容区。

- `role="user"`：只放提问文字。
- 助手消息（默认）：`meta` 放模型、耗时、行数；`children` 依次放 **一句话结论 → ExplainCard → ChartCard / ResultTable → FeedbackBar**；SQL 默认收在右侧面板，正文里只给“查看 SQL”链接。
- `streaming`：流式输出时末尾显示光标；生成过程同步到右侧 Trace。
- 结论句先写数字和对象，不写“根据查询结果显示”。
