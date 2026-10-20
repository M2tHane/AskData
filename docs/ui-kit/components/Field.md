输入框、复选框、开关。标签在上、提示在下，标签永远可见，placeholder 只放示例。

- 输入框高 `control-md`（32px），登录页 `ak-input-lg`（40px）；描边 `line-control`（边框是它唯一的轮廓，≥3:1），聚焦变 `focus` 并加 1px 外环。
- 密码框右侧放「显示 / 隐藏」文字按钮（`ak-reveal`，`aria-pressed`）；检测到大写锁定时下方提示（`data-state="warn"`）。
- 失焦和提交时校验，不在输入中报错；错误用 `data-state="error"`，文字通过 `aria-describedby` 关联。
- 复选框用自绘样式（`ak-cb`）：16px、圆角 4px、`line-control` 描边；勾选 / 半选为 `inverse-bg` 实底 + 2.2px 细线勾或短横，深色主题自动反相。半选用 `data-mixed`，点一下变成全选。
- 开关打开用品牌橙 `action`（CC Switch 同规则）。
