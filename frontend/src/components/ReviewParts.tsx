import { formatValue } from '../lib/format'

/** 结果预览：后端只保存脱敏后的前几行。 */
export function ResultPreview({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = Object.keys(rows[0] ?? {})
  if (!columns.length) return <p className="cb-muted">没有保存结果预览。</p>
  return (
    <div className="cb-preview">
      <table className="cb-data">
        <thead>
          <tr>{columns.map((c) => <th key={c} title={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c) => <td key={c} className="cb-r" title={String(r[c] ?? '')}>{formatValue(r[c])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
