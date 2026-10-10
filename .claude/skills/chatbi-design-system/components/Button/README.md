# Button

按钮：一个视图最多一个 `primary`，其余用 `secondary` 或 `ghost`。

- `variant`：`primary`（黑底，提交/执行/发布）、`secondary`（默认，描边）、`ghost`（工具栏、表格行内）、`danger`（驳回、删除、撤销授权——只用描边红，不用实底红）。
- `size`：`md` 36px（默认）、`sm` 28px（卡片头部、表格行内）。
- `icon` 放在文字前；无文字时成为方形图标按钮，必须传 `aria-label`。
- 行内文字链接用 `.cb-link`（朱红），如“查看 SQL”“打开”。
- 文案用动词开头：“执行查询”“转为评测样例”，不用“确定”“OK”。
