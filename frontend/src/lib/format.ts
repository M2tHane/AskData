export function formatTime(iso: string | undefined, now: Date = new Date()): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diff = Math.round((day(now) - day(d)) / 86_400_000)
  if (diff === 0) return `今天 ${hm}`
  if (diff === 1) return '昨天'
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 带时刻的时间：今天 19:05、昨天 19:05、10-12 19:05，跨年时带年份。 */
export function formatStamp(iso: string, now: Date = new Date()): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diff = Math.round((day(now) - day(d)) / 86_400_000)
  if (diff === 0) return `今天 ${hm}`
  if (diff === 1) return `昨天 ${hm}`
  const md = `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`
  return d.getFullYear() === now.getFullYear() ? md : `${d.getFullYear()}-${md}`
}

export const formatInt = (n: number) => n.toLocaleString('zh-CN')

export function formatMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`
}

/** 分钟级的时长（评测用时、预计耗时）：12 分 20 秒、1 小时 5 分；一分钟以内同 formatMs。 */
export function formatSpan(ms: number): string {
  if (ms < 60_000) return formatMs(Math.round(ms))
  const minutes = Math.round(ms / 60_000)
  if (minutes >= 60) return `${Math.floor(minutes / 60)} 小时${minutes % 60 ? ` ${minutes % 60} 分` : ''}`
  const seconds = Math.round((ms % 60_000) / 1000)
  return ms < 600_000 && seconds ? `${Math.floor(ms / 60_000)} 分 ${seconds} 秒` : `${minutes} 分钟`
}

export type NumberFormat = 'number' | 'integer' | 'percent' | 'currency'

/** 数值列：至少一个数字、其余为空。数字形式的字符串（编号、手机号、邮编）不算，避免被加千分位。 */
export function isNumericColumn(rows: Record<string, unknown>[], column: string): boolean {
  let seen = false
  for (const row of rows) {
    const v = row[column]
    if (v == null) continue
    if (typeof v !== 'number') return false
    seen = true
  }
  return seen
}

/** 列的显示格式：图表配置优先，否则按列名和取值推断。 */
export function columnFormat(column: string, rows: Record<string, unknown>[], formats?: Record<string, NumberFormat>): NumberFormat {
  return formats?.[column] ?? inferFormat(column, rows.map((r) => r[column]))
}

/** Convert value to number: handles numbers and numeric strings; returns null for non-numeric. */
export function toNumber(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v === 'string') {
    const n = Number(v)
    // Match pattern: optional sign, digits with optional decimal point, optional scientific notation
    if (/^-?\d+(\.\d+)?(e[+-]?\d+)?$/i.test(v) && Number.isFinite(n)) return n
    return null
  }
  return null
}

/** Format a numeric value with the given format type (or inferred if not given). */
export function formatValue(v: unknown, fmt?: NumberFormat): string {
  const n = toNumber(v)
  if (n === null) return v == null ? '—' : String(v)

  if (fmt === 'integer') return n.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
  if (fmt === 'percent') return `${(n * 100).toLocaleString('zh-CN', { maximumFractionDigits: n * 100 >= 10 ? 1 : 2 })}%`
  if (fmt === 'currency') return n.toLocaleString('zh-CN', { maximumFractionDigits: 2, minimumFractionDigits: 2 })
  // default: 'number'
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

/** Format a numeric value for chart axes: compact notation for large numbers. */
export function formatAxis(v: unknown, fmt?: NumberFormat): string {
  const n = toNumber(v)
  if (n === null) return v == null ? '—' : String(v)

  if (fmt === 'percent') return `${Math.round(n * 100)}%`
  if (Math.abs(n) >= 1e8) return `${(n / 1e8).toLocaleString('zh-CN', { maximumFractionDigits: 1 })}亿`
  if (Math.abs(n) >= 1e4) return `${(n / 1e4).toLocaleString('zh-CN', { maximumFractionDigits: 1 })}万`
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

/** 一组数共用的单位：最大值到 10 万才用「万」，到 1 亿用「亿」，同一组里不混用。 */
function unitFor(max: number): { div: number; suffix: string } {
  if (max >= 1e8) return { div: 1e8, suffix: '亿' }
  if (max >= 1e5) return { div: 1e4, suffix: '万' }
  return { div: 1, suffix: '' }
}

/**
 * 同一系列的数值标签用同一个单位和同样的小数位，例如都写成「1.21万」「0.84万」，
 * 或都写成「12,148」「8,399」，不会一个带万一个不带。
 */
export function seriesFormatter(values: number[], fmt?: NumberFormat): (v: number) => string {
  if (fmt === 'percent') {
    const max = Math.max(0, ...values.map((v) => Math.abs(v) * 100))
    const digits = max >= 10 ? 1 : 2
    return (v) => `${(v * 100).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`
  }
  const max = Math.max(0, ...values.map(Math.abs))
  const { div, suffix } = unitFor(max)
  const scaled = max / div
  const digits = fmt === 'integer' && div === 1 ? 0 : scaled >= 1000 ? 0 : scaled >= 100 ? 1 : 2
  return (v) => `${(v / div).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })}${suffix}`
}

/** 坐标轴刻度：整组刻度用同一单位，去掉多余的 0（「1.5万」「2万」）。 */
export function axisFormatter(ticks: number[], fmt?: NumberFormat): (v: number) => string {
  if (fmt === 'percent') return (v) => `${Number((v * 100).toFixed(1)).toLocaleString('zh-CN')}%`
  const { div, suffix } = unitFor(Math.max(0, ...ticks.map(Math.abs)))
  return (v) => (v === 0 ? '0' : `${Number((v / div).toFixed(2)).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}${suffix}`)
}

/** 午夜时间戳和月初日期写得短一些：「2026-01-01T00:00:00」→「2026-01-01」；一整列都是月初时 →「2026-01」。 */
export function formatDateLabels(labels: string[]): string[] {
  const day = labels.map((l) => l.replace(/^(\d{4}-\d{2}-\d{2})[T ]00:00:00(?:\.0+)?(?:Z|[+-]00(?::?00)?)?$/, '$1'))
  const months = day.length > 1 && day.every((l) => /^\d{4}-\d{2}-01$/.test(l))
  return months ? day.map((l) => l.slice(0, 7)) : day
}

/** Infer the format type from a column name and sample values. */
export function inferFormat(column: string, values: unknown[]): NumberFormat {
  const pattern = /(rate|ratio|margin|pct|percent|share|率|占比|比例)/i
  if (pattern.test(column)) {
    const nums = values.map(toNumber).filter((n): n is number => n !== null)
    if (nums.length > 0 && nums.every((n) => n >= -1.5 && n <= 1.5)) return 'percent'
  }
  const nums = values.map(toNumber).filter((n): n is number => n !== null)
  if (nums.length > 0 && nums.every((n) => Number.isInteger(n))) return 'integer'
  return 'number'
}
