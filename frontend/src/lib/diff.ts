export interface DiffLine {
  kind: 'same' | 'add' | 'del'
  text: string
}

/** 逐行对比：最长公共子序列，提示词只有几十到几百行，足够快。 */
export function lineDiff(before: string, after: string): DiffLine[] {
  const a = before.split('\n')
  const b = after.split('\n')
  const lcs: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0))
  for (let i = a.length - 1; i >= 0; i -= 1)
    for (let j = b.length - 1; j >= 0; j -= 1)
      lcs[i]![j] = a[i] === b[j] ? (lcs[i + 1]![j + 1] ?? 0) + 1 : Math.max(lcs[i + 1]![j] ?? 0, lcs[i]![j + 1] ?? 0)
  const out: DiffLine[] = []
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: 'same', text: a[i] ?? '' })
      i += 1
      j += 1
    } else if ((lcs[i + 1]![j] ?? 0) >= (lcs[i]![j + 1] ?? 0)) {
      out.push({ kind: 'del', text: a[i] ?? '' })
      i += 1
    } else {
      out.push({ kind: 'add', text: b[j] ?? '' })
      j += 1
    }
  }
  for (; i < a.length; i += 1) out.push({ kind: 'del', text: a[i] ?? '' })
  for (; j < b.length; j += 1) out.push({ kind: 'add', text: b[j] ?? '' })
  return out
}

export const changed = (lines: DiffLine[]) => lines.filter((l) => l.kind !== 'same').length

const VARIABLE = /\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?/g

/** 提示词里用到的变量名；与登记的变量不一致时后端会拒绝保存。 */
export function usedVariables(text: string): string[] {
  return [...new Set([...text.matchAll(VARIABLE)].map((m) => m[1] ?? ''))].sort()
}

export function templateProblems(text: string, required: string[]): string[] {
  const problems: string[] = []
  const used = usedVariables(text)
  const missing = required.filter((v) => !used.includes(v))
  const extra = used.filter((v) => !required.includes(v))
  if (missing.length) problems.push(`缺少变量 ${missing.map((v) => '$' + v).join('、')}`)
  if (extra.length) problems.push(`不能使用未登记的变量 ${extra.map((v) => '$' + v).join('、')}`)
  if (required.includes('data') && !text.includes('<untrusted_data>$data</untrusted_data>')) problems.push('必须保留 <untrusted_data>$data</untrusted_data> 数据块')
  if (!text.trim()) problems.push('内容不能为空')
  return problems
}
