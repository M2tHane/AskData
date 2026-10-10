/**
 * 需要新后端接口的功能：界面先做好，用 VITE_FEATURE_* 开关默认隐藏。
 * 在 .env.local 里写 VITE_FEATURE_DASHBOARD=1 之类即可打开。
 */
const on = (v: string | boolean | undefined) => v === true || v === '1' || v === 'true'

export const FEATURES = {
  /** 我的看板 + 回答上的「固定到看板」。 */
  dashboard: on(import.meta.env.VITE_FEATURE_DASHBOARD),
  /** 评测失败题「重跑这道题」。 */
  evalRerunCase: on(import.meta.env.VITE_FEATURE_EVAL_RERUN_CASE),
  /** 指标「近 30 天引用」列和按引用排序。 */
  metricRefs: on(import.meta.env.VITE_FEATURE_METRIC_REFS),
}
