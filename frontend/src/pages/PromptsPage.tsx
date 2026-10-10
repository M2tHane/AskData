import { useState } from 'react'
import type { ReactNode } from 'react'
import { fetchRevision, usePromptActions, usePromptDetail, usePrompts } from '../api/hooks'
import type { PromptDetail, PromptSummary, RevisionMeta } from '../api/types'
import { Badge, Button, TextField } from '../components/basic'
import { Dialog, DialogFooter, UnsavedDialog } from '../components/Dialog'
import { Icon } from '../components/Icon'
import { PromptEditor } from '../components/PromptEditor'
import { useToast } from '../components/Toast'
import { changed, lineDiff, templateProblems } from '../lib/diff'
import { ApiError, describeError } from '../lib/errors'
import { formatTime } from '../lib/format'
import { detectFormat, formatPrompt, validateFormat } from '../lib/promptFormat'
import { useUnsaved, useUnsavedGuard } from '../state/unsaved'

const sel = (p: Pick<PromptSummary, 'kind' | 'key'>) => `${p.kind}:${p.key}`

/** 可折叠的卡片：点标题折叠或展开。 */
function FoldCard(props: { title: string; count?: string; open: boolean; onToggle: () => void; children: ReactNode }) {
  return (
    <section className="cb-panel cb-pr-card">
      <button type="button" className="cb-pr-fold" aria-expanded={props.open} onClick={props.onToggle}>
        <Icon name="chevronDown" size="sm" />
        <h3 className="cb-h">{props.title}</h3>
        {props.count ? <span className="cb-faint">{props.count}</span> : null}
      </button>
      {props.open ? <div className="cb-pr-card-body">{props.children}</div> : null}
    </section>
  )
}

/** 管理员在线编辑系统提示词和方言规则：每次保存都是一个新版本，可随时启用任一历史版本或恢复内置版本。 */
export function PromptsPage() {
  const prompts = usePrompts()
  const [picked, setPicked] = useState<string | null>(null)
  const [listOpen, setListOpen] = useState(true)
  const [history, setHistory] = useState(true)
  const [leaving, setLeaving] = useState<string | null>(null)
  const [leavingSave, setLeavingSave] = useState(false)
  const dirty = useUnsaved((s) => s.dirty)
  const items = prompts.data ?? []
  const current = items.find((p) => sel(p) === picked) ?? items[0]
  const pick = (id: string) => {
    if (current && id === sel(current)) return
    if (dirty) setLeaving(id)
    else setPicked(id)
  }
  const group = (kind: 'system' | 'dialect') => (
    <ul className="cb-pr-list">
      {items.filter((p) => p.kind === kind).map((p) => {
        const custom = !p.active_label.includes('（内置）')
        return (
          <li key={sel(p)} aria-current={current ? sel(p) === sel(current) : undefined}>
            <button type="button" onClick={() => pick(sel(p))} title={p.revision_count ? `共 ${p.revision_count} 个历史版本` : undefined}>
              <span className="cb-pr-name">{p.label}</span>
              {custom ? <Badge tone="info">已修改</Badge> : <span className="cb-faint">内置</span>}
              {custom ? <span className="cb-pr-ver">{p.active_label.replace('（内置）', '')}</span> : null}
            </button>
          </li>
        )
      })}
    </ul>
  )
  const list = (
    <FoldCard title="提示词" count={items.length ? `${items.length} 个` : undefined} open={listOpen} onToggle={() => setListOpen(!listOpen)}>
      <div className="cb-eyebrow">系统提示词</div>
      {group('system')}
      <div className="cb-eyebrow">数据库方言规则</div>
      {group('dialect')}
    </FoldCard>
  )
  const leave = () => {
    if (leaving) setPicked(leaving)
    setLeaving(null)
  }
  return (
    <div className="cb-page">
      {current ? (
        <PromptWorkspace key={sel(current)} kind={current.kind} name={current.key} list={list} history={history} onHistory={() => setHistory(!history)} />
      ) : (
        <div className="cb-pr-layout">
          {list}
          <section className="cb-panel cb-pr-card"><div className="cb-pr-card-body cb-muted">{prompts.isError ? '加载失败，请稍后重试。' : '正在加载…'}</div></section>
        </div>
      )}
      {leaving ? (
        <UnsavedDialog
          onStay={() => setLeaving(null)}
          onDiscard={leave}
          saving={leavingSave}
          onSave={() => {
            setLeavingSave(true)
            void (useUnsaved.getState().save?.() ?? Promise.resolve(false)).then((ok) => {
              setLeavingSave(false)
              if (ok) leave()
            })
          }}
        />
      ) : null}
    </div>
  )
}

interface WorkspaceProps {
  kind: 'system' | 'dialect'
  name: string
  list: ReactNode
  history: boolean
  onHistory: () => void
}

function PromptWorkspace(props: WorkspaceProps) {
  const detail = usePromptDetail(props.kind, props.name)
  if (!detail.data) {
    return (
      <div className="cb-pr-layout">
        {props.list}
        <section className="cb-panel cb-pr-card"><div className="cb-pr-card-body cb-muted">{detail.isError ? '加载失败，请稍后重试。' : '正在加载…'}</div></section>
      </div>
    )
  }
  // 发布或切换版本后 active_label 变化，编辑区随之重置为当前生效的文本。
  return <Workspace key={detail.data.active_label} detail={detail.data} {...props} />
}

function Workspace({ detail, list, history, onHistory }: WorkspaceProps & { detail: PromptDetail }) {
  const toast = useToast()
  const actions = usePromptActions(detail.kind, detail.key)
  const [draft, setDraft] = useState(detail.active_content)
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState('')
  const [diff, setDiff] = useState<{ title: string; lines: ReturnType<typeof lineDiff> } | null>(null)
  const [confirmReset, setConfirmReset] = useState(false)
  const format = detectFormat(draft)
  const issue = validateFormat(draft, format)
  const problems = detail.kind === 'system' ? templateProblems(draft, detail.variables) : draft.trim() ? [] : ['内容不能为空']
  const invalid = problems.length > 0 || issue != null
  const dirty = draft !== detail.active_content
  const busy = actions.publish.isPending || actions.activate.isPending || actions.reset.isPending
  const fail = (e: unknown) => toast({ tone: 'danger', title: '操作失败', message: e instanceof ApiError ? describeError(e.code) : undefined })

  // 离开页面或切换提示词时的「保存并离开」：存为新版本但不启用，避免未经确认就影响所有人的查数结果。
  useUnsavedGuard(
    dirty,
    async () => {
      if (invalid) {
        toast({ tone: 'danger', title: '无法保存', message: first(issue, problems) })
        return false
      }
      try {
        const r = await actions.publish.mutateAsync({ content: draft, note: '离开页面时保存', activate: false })
        toast({ tone: 'success', title: `已保存为 r${r.revision}`, message: '未启用，可在历史版本里启用。' })
        return true
      } catch (e) {
        fail(e)
        return false
      }
    },
  )

  const publish = (activate: boolean) =>
    actions.publish.mutate(
      { content: draft, note, activate },
      {
        onSuccess: (r) => {
          setSaving(false)
          toast({ tone: 'success', title: activate ? `已启用 r${r.revision}` : `已保存为 r${r.revision}`, message: activate ? undefined : '未启用，可在历史版本里启用。' })
        },
        onError: fail,
      },
    )
  const reformat = () => {
    const out = formatPrompt(draft, format)
    if (out == null) toast({ tone: 'warning', title: '无法格式化', message: issue ? `先修好第 ${issue.line} 行的语法错误。` : undefined })
    else if (out === draft) toast({ tone: 'info', title: '格式已经是整齐的' })
    else setDraft(out)
  }
  const load = async (r: RevisionMeta) => setDraft((await fetchRevision(detail.kind, detail.key, r.id)).content)
  const compare = async (r: RevisionMeta) => setDiff({ title: `r${r.revision} 与当前生效版本（${detail.active_label}）`, lines: lineDiff((await fetchRevision(detail.kind, detail.key, r.id)).content, detail.active_content) })
  const seedActive = detail.revisions.every((r) => !r.is_active)

  return (
    <div className="cb-pr-layout">
      <div className="cb-pr-side">
        {list}
        <FoldCard title="历史版本" count={`${detail.revisions.length + 1} 个版本`} open={history} onToggle={onHistory}>
          <ul className="cb-pr-revs">
            {detail.revisions.map((r) => (
              <li key={r.id} data-active={r.is_active || undefined}>
                <div className="cb-pr-rev-head">
                  <span className="cb-m">r{r.revision}</span>
                  {r.is_active ? <Badge tone="success">生效中</Badge> : null}
                  <span className="cb-spacer" />
                  <span className="cb-faint">{formatTime(r.created_at)}</span>
                </div>
                <div className="cb-pr-rev-note">{r.note || '未填写说明'}</div>
                <div className="cb-pr-rev-foot">
                  <span className="cb-faint">{r.author || '—'}</span>
                  <span className="cb-spacer" />
                  <Button size="sm" variant="ghost" onClick={() => void load(r)}>载入编辑</Button>
                  {r.is_active ? null : <Button size="sm" variant="ghost" onClick={() => void compare(r)}>对比</Button>}
                  {r.is_active ? null : <Button size="sm" variant="ghost" disabled={busy} onClick={() => actions.activate.mutate(r.id, { onSuccess: () => toast({ tone: 'success', title: `已切换到 r${r.revision}` }), onError: fail })}>启用</Button>}
                </div>
              </li>
            ))}
            <li data-active={seedActive || undefined}>
              <div className="cb-pr-rev-head">
                <span className="cb-m">{detail.seed_version}</span>
                <Badge>内置</Badge>
                {seedActive ? <Badge tone="success">生效中</Badge> : null}
              </div>
              <div className="cb-pr-rev-note">随程序发布，不可修改</div>
              <div className="cb-pr-rev-foot">
                <span className="cb-spacer" />
                <Button size="sm" variant="ghost" onClick={() => setDraft(detail.seed_content)}>载入编辑</Button>
                {seedActive ? null : <Button size="sm" variant="ghost" onClick={() => setDiff({ title: `内置 ${detail.seed_version} 与当前生效版本（${detail.active_label}）`, lines: lineDiff(detail.seed_content, detail.active_content) })}>对比</Button>}
                {seedActive ? null : <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirmReset(true)}>启用</Button>}
              </div>
            </li>
          </ul>
        </FoldCard>
      </div>

      <section className="cb-panel cb-pr-main">
        <div className="cb-pr-main-head">
          <h3 className="cb-h">{detail.label}</h3>
          <span className="cb-spacer" />
          {dirty ? <Badge tone="warning">有未保存的修改</Badge> : null}
          <span className="cb-faint cb-pr-nowrap">当前生效 <span className="cb-m">{detail.active_label}</span></span>
          <Button variant="ghost" disabled={!dirty} onClick={() => setDiff({ title: `未保存的修改（对比 ${detail.active_label}）`, lines: lineDiff(detail.active_content, draft) })}>查看修改</Button>
          <Button variant="ghost" disabled={!dirty} onClick={() => setDraft(detail.active_content)}>放弃修改</Button>
          <Button variant="primary" disabled={busy || invalid || !dirty} onClick={() => setSaving(true)}>保存</Button>
        </div>
        <p className="cb-muted">{detail.description}</p>
        <PromptEditor label="提示词内容" value={draft} onChange={setDraft} variables={detail.variables} format={format} issue={issue} problems={problems} onFormat={reformat} />
      </section>

      {saving ? (
        <Dialog title={`保存「${detail.label}」的新版本`} onClose={() => setSaving(false)}>
          <TextField label="版本说明" hint="写清楚改了什么，之后在历史版本里更容易找" maxLength={200} autoFocus value={note} onChange={(e) => setNote(e.target.value)} />
          <DialogFooter>
            <Button variant="ghost" onClick={() => setSaving(false)}>取消</Button>
            <Button disabled={busy} onClick={() => publish(false)}>仅保存为新版本</Button>
            <Button variant="primary" disabled={busy} onClick={() => publish(true)}>保存并启用</Button>
          </DialogFooter>
        </Dialog>
      ) : null}
      {diff ? (
        <Dialog title={diff.title} size="xl" onClose={() => setDiff(null)}>
          {changed(diff.lines) === 0 ? <div className="cb-muted">没有差异。</div> : (
            <div className="cb-pr-diff" aria-label="差异">
              {diff.lines.map((l, i) => <div key={i} data-kind={l.kind}>{l.kind === 'add' ? '+ ' : l.kind === 'del' ? '- ' : '  '}{l.text}</div>)}
            </div>
          )}
          <DialogFooter><Button variant="ghost" onClick={() => setDiff(null)}>关闭</Button></DialogFooter>
        </Dialog>
      ) : null}
      {confirmReset ? (
        <Dialog title="恢复内置版本" onClose={() => setConfirmReset(false)}>
          <p>停用所有自定义版本，改用程序自带的 {detail.seed_version}。历史版本都会保留，之后仍可再启用。</p>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmReset(false)}>取消</Button>
            <Button variant="primary" disabled={busy} onClick={() => actions.reset.mutate(undefined, { onSuccess: () => { setConfirmReset(false); toast({ tone: 'success', title: '已恢复内置版本' }) }, onError: fail })}>恢复内置版本</Button>
          </DialogFooter>
        </Dialog>
      ) : null}
    </div>
  )
}

function first(issue: { line: number; message: string } | null, problems: string[]) {
  return issue ? `第 ${issue.line} 行：${issue.message}` : problems[0]
}
