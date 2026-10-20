SQL 面板：在 `ChatTurn` 底部点「SQL」展开后出现，沿用 `SqlBlock` 的能力（复制、编辑后重跑、只读校验徽标），外观换成新 token。

- 头部 `bg-card`，正文 `bg-subtle` + `code` 等宽，长行折行不横滚，圆角 `radius-panel`。
- 关键字 `code-keyword`、字符串 `code-string`、数字 `code-number`。
- 头部徽标：通过校验是 success「只读 · 已校验」，被拦截是 danger「被拦截 · 未执行」，手改后是 warning「已手动修改」。
