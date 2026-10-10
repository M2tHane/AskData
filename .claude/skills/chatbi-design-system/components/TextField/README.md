# TextField

单行输入框：表单、搜索、连接配置。

- 原生 input 属性透传；另有 `label`、`hint`、`error`、`icon`、`suffix`。
- 描边用 `line-control`（≥3:1），聚焦时 2px `focus` 环。错误时描边与提示变 `danger`，提示写清怎么改。
- API Key 等密钥只显示掩码和“已加密保存”，不回显明文。
