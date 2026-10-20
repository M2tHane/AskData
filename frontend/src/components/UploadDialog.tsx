import { useEffect, useRef, useState } from 'react'
import { confirmUpload, getUpload, previewUpload, publishUpload, repreviewUpload } from '../api/hooks'
import type { UploadColumn, UploadJob, UploadParseConfig, UploadPreview } from '../api/types'
import { ApiError, describeError } from '../lib/errors'
import { Button, Callout, TextField } from './basic'
import { Dialog, DialogFooter } from './Dialog'
import { Icon } from './Icon'
import { Select } from './Select'

const MAX_MB = 50
const TYPES: { value: UploadColumn['data_type']; label: string }[] = [
  { value: 'text', label: '文本' },
  { value: 'integer', label: '整数' },
  { value: 'number', label: '小数' },
  { value: 'date', label: '日期' },
  { value: 'datetime', label: '日期时间' },
  { value: 'boolean', label: '是/否' },
]
const ASCII_NAME = /^[a-z_][a-z0-9_]*$/
const FINISHED = new Set(['success', 'failed', 'registration_pending'])

function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '')
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

/** 文件名里能用作表名的部分；中文文件名退回 data。 */
function tableNameOf(filename: string): string {
  const base = filename.replace(/\.[^.]+$/, '').toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '')
  if (!base) return 'data'
  return (/^\d/.test(base) ? `t_${base}` : base).slice(0, 55)
}

interface Draft {
  preview: UploadPreview & { sheets: string[]; warnings: string[] }
  /** 原表头（预览时的说明），只读展示。 */
  headers: string[]
  columns: UploadColumn[]
  tableName: string
  tableComment: string
  headerRow: number
}

function draftOf(raw: UploadPreview, file: string, previous?: Draft): Draft {
  const preview = { ...raw, sheets: raw.sheets ?? [], warnings: raw.warnings ?? [] }
  return {
    preview,
    headers: preview.columns.map((c) => c.description || c.name),
    columns: preview.columns.map((c) => ({ ...c, description: '' })),
    tableName: previous?.tableName ?? preview.config.table_name ?? tableNameOf(file),
    tableComment: previous?.tableComment ?? (preview.config.table_comment || file.replace(/\.[^.]+$/, '')),
    headerRow: preview.config.header_row ?? 1,
  }
}

/**
 * Excel / CSV 上传：选文件 → 确认表名和字段 → 导入并发布。
 * datasourceId 为空时新建一个上传型数据源；否则向它追加或覆盖一张表。
 */
export function UploadDialog(props: { datasourceId?: number; datasourceName?: string; onBack?: () => void; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState(props.datasourceName ?? '')
  const [file, setFile] = useState<File | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [mode, setMode] = useState<'overwrite' | 'append'>('overwrite')
  const [job, setJob] = useState<UploadJob | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const existing = props.datasourceId != null

  const fail = (e: unknown) => setError(e instanceof ApiError ? describeError(e.code) : '读取文件失败，请确认格式后重试。')

  const choose = async (picked: File | undefined) => {
    setError(null)
    if (!picked) return
    if (!/\.(csv|xlsx|xls)$/i.test(picked.name)) return setError('只支持 .xlsx、.xls、.csv 文件。')
    if (picked.size > MAX_MB * 1024 * 1024) return setError(`文件超过 ${MAX_MB} MB。`)
    setFile(picked)
    setBusy(true)
    try {
      const preview = await previewUpload({
        filename: picked.name,
        content_base64: await toBase64(picked),
        datasource_id: props.datasourceId,
        datasource_name: name.trim() || picked.name.replace(/\.[^.]+$/, ''),
        config: { table_name: tableNameOf(picked.name) },
      })
      setDraft(draftOf(preview, picked.name))
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  const reload = async (patch: Partial<UploadParseConfig>) => {
    if (!draft || !file) return
    setBusy(true)
    setError(null)
    try {
      const preview = await repreviewUpload(draft.preview.id, { ...draft.preview.config, columns: [], table_name: draft.tableName, ...patch })
      setDraft(draftOf(preview, file.name, draft))
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  // 导入在后台任务里执行：轮询到成功、失败或待发布为止。
  useEffect(() => {
    if (!job || FINISHED.has(job.status)) return
    const timer = window.setTimeout(() => {
      getUpload(job.id).then(setJob, fail)
    }, 1200)
    return () => window.clearTimeout(timer)
  }, [job])

  const { onDone } = props
  useEffect(() => {
    if (job?.status === 'success') onDone()
  }, [job?.status, onDone])

  const submit = async () => {
    if (!draft) return
    setBusy(true)
    setError(null)
    try {
      const config: UploadParseConfig = { ...draft.preview.config, columns: draft.columns, table_name: draft.tableName, table_comment: draft.tableComment, header_row: draft.headerRow }
      setJob(await confirmUpload(draft.preview.id, config, existing ? mode : 'overwrite'))
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  const republish = async () => {
    if (!job) return
    setBusy(true)
    try {
      setJob(await publishUpload(job.id))
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  const setColumn = (i: number, patch: Partial<UploadColumn>) =>
    setDraft((d) => (d ? { ...d, columns: d.columns.map((c, j) => (j === i ? { ...c, ...patch } : c)) } : d))

  const badNames = draft ? draft.columns.filter((c) => !ASCII_NAME.test(c.name) || /^column_\d+$/.test(c.name)).length : 0
  const duplicate = draft ? new Set(draft.columns.map((c) => c.name)).size !== draft.columns.length : false
  const tableOk = draft ? ASCII_NAME.test(draft.tableName) : false
  const title = existing ? `上传新数据 · ${props.datasourceName ?? ''}` : '新建数据源 · Excel / CSV'

  if (job) {
    const running = !FINISHED.has(job.status)
    return (
      <Dialog title={title} onClose={props.onClose}>
        {running ? <Callout tone="info" icon="refresh" title="正在导入">{job.filename}：解析并写入数据，完成后自动发布。</Callout> : null}
        {job.status === 'success' ? <Callout tone="success" title="导入完成">已写入 {job.row_count.toLocaleString()} 行，表结构已同步，可以在「表与字段」里调整可见范围。</Callout> : null}
        {job.status === 'registration_pending' ? <Callout tone="warning" title="数据已写入，发布未完成">可以重试发布，不会重复写入数据。</Callout> : null}
        {job.status === 'failed' ? <Callout tone="danger" title="导入失败">{job.error ? describeError(job.error) : '请检查文件内容和字段类型后重试。'}</Callout> : null}
        {error ? <Callout tone="danger">{error}</Callout> : null}
        <DialogFooter>
          {job.status === 'registration_pending' ? <Button variant="primary" disabled={busy} onClick={() => void republish()}>重试发布</Button> : null}
          {job.status === 'failed' ? <Button onClick={() => setJob(null)}>返回修改</Button> : null}
          <Button variant={running ? 'ghost' : 'primary'} onClick={props.onClose}>{running ? '后台继续' : '完成'}</Button>
        </DialogFooter>
      </Dialog>
    )
  }

  return (
    <Dialog title={title} size={draft ? 'xl' : 'lg'} onClose={props.onClose}>
      {!draft ? (
        <div className="cb-form" style={{ maxWidth: 'none' }}>
          {existing ? null : <TextField label="数据源名称" placeholder="不填则使用文件名" value={name} onChange={(e) => setName(e.target.value)} />}
          <div
            className="cb-drop"
            role="button"
            tabIndex={0}
            data-active={dragging || undefined}
            onClick={() => input.current?.click()}
            onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragging(false)
              void choose(e.dataTransfer.files[0])
            }}
          >
            <Icon name="upload" />
            <span className="cb-drop-title">{busy ? `正在读取 ${file?.name ?? ''}…` : '点击选择或拖入文件'}</span>
            <span>支持 .xlsx、.xls、.csv，单个文件不超过 {MAX_MB} MB；第一行作为表头</span>
          </div>
          <input ref={input} type="file" hidden accept=".xlsx,.xls,.csv" onChange={(e) => void choose(e.target.files?.[0] ?? undefined)} />
          {error ? <Callout tone="danger">{error}</Callout> : null}
        </div>
      ) : (
        <div className="cb-form" style={{ maxWidth: 'none' }}>
          <div className="cb-file-card">
            <img src="/datasource-icons/icon_excel.png" alt="" width={28} height={28} />
            <div className="cb-file-meta">
              <b>{file?.name}</b>
              <span className="cb-muted">预览前 {draft.preview.rows.length} 行 · {draft.columns.length} 列</span>
            </div>
            <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>换文件</Button>
          </div>
          <div className="cb-upload-grid">
            {draft.preview.sheets.length > 1 ? (
              <div className="cb-field">
                <label htmlFor="up-sheet">工作表</label>
                <Select id="up-sheet" value={typeof draft.preview.config.sheet === 'number' ? draft.preview.sheets[draft.preview.config.sheet] : draft.preview.config.sheet} disabled={busy} onChange={(sheet) => void reload({ sheet })} options={draft.preview.sheets.map((s) => ({ value: s, label: s }))} />
              </div>
            ) : null}
            <TextField label="表名" value={draft.tableName} error={tableOk ? undefined : '小写字母、数字、下划线，不以数字开头'} onChange={(e) => setDraft({ ...draft, tableName: e.target.value })} />
            <TextField label="表说明" value={draft.tableComment} onChange={(e) => setDraft({ ...draft, tableComment: e.target.value })} />
            <TextField label="表头所在行" type="number" min={1} value={draft.headerRow} onChange={(e) => setDraft({ ...draft, headerRow: Math.max(1, Number(e.target.value) || 1) })} onBlur={() => draft.headerRow !== (draft.preview.config.header_row ?? 1) && void reload({ header_row: draft.headerRow })} />
            {existing ? (
              <div className="cb-field">
                <label htmlFor="up-mode">写入方式</label>
                <Select id="up-mode" value={mode} onChange={setMode} options={[{ value: 'overwrite', label: '覆盖同名表' }, { value: 'append', label: '追加到同名表' }]} />
              </div>
            ) : null}
          </div>
          {draft.preview.warnings.length ? (
            <Callout tone="warning" title="请确认这些列的类型">
              {draft.preview.warnings.map((w) => {
                const [index, text] = w.split(': ', 2)
                return <div key={w}>{`${draft.headers[Number(index) - 1] ?? `第 ${index} 列`}：${text ?? w}`}</div>
              })}
            </Callout>
          ) : null}
          <div className="cb-table-wrap" style={{ maxHeight: 360 }}>
            <table className="cb-data cb-upload-cols">
              <thead>
                <tr>
                  <th>原列名</th>
                  <th>字段名（英文）</th>
                  <th>类型</th>
                  <th>补充说明</th>
                  <th>示例</th>
                </tr>
              </thead>
              <tbody>
                {draft.columns.map((c, i) => {
                  const bad = !ASCII_NAME.test(c.name) || /^column_\d+$/.test(c.name)
                  return (
                    <tr key={i}>
                      <td title={draft.headers[i]}>{draft.headers[i]}</td>
                      <td>
                        <div className={`cb-input cb-input-sm${bad ? ' cb-field-error' : ''}`}>
                          <input aria-label={`${draft.headers[i]} 字段名`} aria-invalid={bad || undefined} value={c.name} onChange={(e) => setColumn(i, { name: e.target.value })} />
                        </div>
                      </td>
                      <td>
                        <Select size="sm" ariaLabel={`${draft.headers[i]} 类型`} value={c.data_type} onChange={(data_type) => setColumn(i, { data_type })} options={TYPES} />
                      </td>
                      <td>
                        <div className="cb-input cb-input-sm">
                          <input aria-label={`${draft.headers[i]} 补充说明`} placeholder="可选" value={c.description} onChange={(e) => setColumn(i, { description: e.target.value })} />
                        </div>
                      </td>
                      <td className="cb-muted" title={draft.preview.rows.slice(0, 3).map((r) => String(r[i] ?? '')).join('、')}>
                        {draft.preview.rows.slice(0, 3).map((r) => String(r[i] ?? '')).filter(Boolean).join('、') || '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {badNames ? <Callout tone="warning">{badNames} 个字段还没有英文名：中文列名会作为字段说明保留，问数时照样能用中文提问。</Callout> : null}
          {duplicate ? <Callout tone="danger">字段名重复，请修改。</Callout> : null}
          {error ? <Callout tone="danger">{error}</Callout> : null}
        </div>
      )}
      <DialogFooter>
        {props.onBack && !draft ? <Button variant="ghost" onClick={props.onBack}>上一步</Button> : null}
        <Button variant="ghost" onClick={props.onClose}>取消</Button>
        {draft ? (
          <Button variant="primary" icon="upload" disabled={busy || !!badNames || duplicate || !tableOk} onClick={() => void submit()}>
            导入并发布
          </Button>
        ) : null}
      </DialogFooter>
    </Dialog>
  )
}
