登录页的四种状态，放在同一表单位置里替换，不弹窗。

- 密码错误（`AUTH_FAILED`）：主按钮上方 `Callout` danger + 密码框 `data-state="error"`；文案不区分「账号不存在」和「密码错」。
- 大写锁定：用 `getModifierState('CapsLock')` 检测，提示放在密码框下方，用 `warning`。显示密码后按钮文字变「隐藏」。
- 请求过于频繁（`RATE_LIMITED`）：`Callout` warning，说明稍后再试、急用找谁，主按钮暂时禁用。
- 提交中：按钮禁用、文字变「登录中…」，防止重复提交。
