# AskData UI Kit

AskData 前端界面重构的设计规范包，视觉参照 CC Switch v7，图表沿用 AskData 原有配色。

把整个目录放到仓库的 `docs/ui-kit/`，再把 `REFACTOR_PROMPT.md` 交给 Claude Code。

| 路径 | 内容 |
|---|---|
| `REFACTOR_PROMPT.md` | 交给 Claude Code 执行重构的任务说明 |
| `DESIGN.md` | 设计规范总纲：语气、颜色、字体、间距、布局、交互规则、迁移对照 |
| `tokens/tokens.json` | 唯一的取值来源（颜色分浅色 / 深色两套） |
| `tokens/tokens.css` | 由 tokens.json 生成；浅色为默认，`<html data-theme="dark">` 切深色，未设置时跟随系统 |
| `tokens/legacy-aliases.css` | 迁移期间把旧变量名（`--canvas`、`--ink`…）指到新 token，迁完删除 |
| `styles/components.css` | 组件参考实现，类名前缀 `ak-`（落到代码里改回项目的 `cb-`） |
| `styles/runtime.js` | 预览用的交互脚本（图表绘制、Tab、弹窗、通知、分页、可用字段面板）。只作行为参考，不要搬进产品 |
| `components/*.md` | 每个组件和每个整页的用法与规则；`Page*.md` 是各模块的页面说明 |
| `previews/*.html` | 可直接在浏览器打开的静态预览，可交互；地址后加 `#dark` 看深色 |
| `screenshots/*-{light,dark}.png` | 每个预览在浅色和深色下的截图，作为验收基准 |
| `prototype/askdata-prototype.html` | 整站可交互原型：登录 → 13 个模块，可切预览角色（管理员 / 负责人 / 员工）和深浅色；地址后加 `#logs` 之类可直达某页 |
| `assets/icons.json` | 24×24 线性图标路径（含新增的 `pin`、`grid`、`user`、`settings`、`more`） |

页面稿中带橙色虚线「新增」小标的，是本次建议补上、现有代码里还没有的功能；这个小标只用于设计稿，不要带进产品。
