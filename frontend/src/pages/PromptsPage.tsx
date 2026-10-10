import { useState } from 'react'
import type { ReactNode } from 'react'
import { fetchRevision, usePromptActions, usePromptDetail, usePrompts } from '../api/hooks'
import type { PromptDetail, PromptSummary, RevisionMeta } from '../api/types'
import { Badge, Button, Callout, Panel, TextField } from '../components/basic'
import { Dialog, DialogFooter } from '../components/Dialog'
import { Icon } from '../components/Icon'
import { PromptEditor } from '../components/PromptEditor'
import { useToast } from '../components/Toast'
import { changed, lineDiff, templateProblems } from '../lib/diff'
import { ApiError, describeError } from '../lib/errors'
import { formatTime } from '../lib/format'

const sel = (p: Pick<PromptSummary, 'kind' | 'key'>) => `${p.kind}:${p.key}`

/** 卡片右上角的展开/收起按钮。 */
function Fold(props: { open: boolean; onToggle: () => void }) {
  return (
    <button type="button" className="cb-fold" aria-expanded={props.open} onClick={props.onToggle}>
      {props.open ? '收起' : '展开'}
      <Icon name="chevronDown" size="sm" className="cb-fold-chevron" />
    </button>
  )
}

/** 管理员在线编辑系统提示词和方言规则：每次保存都是一个新版本，可随时启用任一历史版本或恢复内置版本。 */
export function PromptsPage() {
  const prompts = usePrompts()
  const [picked, setPicked] = useState<string | null>(null)
  const [listOpen, setListOpen] = useState(true)
  const [history, setHistory] = useState(true)
  const items = prompts.data ?? []
  const current = items.find((p) => sel(p) === picked) ?? items[0]
  const group = (kind: 'system' | 'dialect') => (
    <ul className="cb-prompt-list">
      {items.filter((p) => p.kind === kind).map((p) => {
        const custom = !p.active_label.includes('（内置）')
        return (
          <li key={sel(p)} aria-current={current ? sel(p) === sel(current) : undefined}>
            <button type="button" onClick={() => setPicked(sel(p))} title={p.revision_count ? `共 ${p.revision_count} 个历史版本` : undefined}>
              <span className="cb-prompt-name">{p.label}</span>
              {custom ? <Badge tone="info">已修改</Badge> : null}
              <span className="cb-prompt-ver">{p.active_label.replace('（内置）', '')}</span>
            </button>
          </li>
        )
      })}
    </ul>
  )
  const list = (
    <Panel title="提示词" className={listOpen ? undefined : 'cb-panel-folded'} actions={<Fold open={listOpen} onToggle={() => setListOpen(!listOpen)} />}>
      {listOpen ? (
        <>
          <div className="cb-prompt-group">
            <div className="cb-eyebrow">系统提示词</div>
            {group('system')}
          </div>
          <div className="cb-prompt-group">
            <div className="cb-eyebrow">数据库方言规则</div>
            {group('dialect')}
          </div>
        </>
      ) : null}
    </Panel>
  )
  return (
    <div className="cb-page">
      <Callout tone="warning" title="修改会立即影响所有人的查数结果">
        提示词只能改文字，变量和数据块由系统固定；模型生成的 SQL 仍然要经过 SQL 守卫。建议启用新版本后到「评测」页跑一遍，结果不好可以在历史版本里一键切回。
      </Callout>
      {current ? (
        <PromptWorkspace key={sel(current)} kind={current.kind} name={current.key} list={list} history={history} onHistory={() => setHistory(!history)} />
      ) : (
        <div className="cb-prompt-layout">
          {list}
          <Panel><div className="cb-muted">正在加载…</div></Panel>
        </div>
      )}
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
      <div className="cb-prompt-layout">
        {props.list}
        <Panel><div className="cb-muted">{detail.isError ? '加载失败，请稍后重试。' : '正在加载…'}</div></Panel>
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
  const problems = detail.kind === 'system' ? templateProblems(draft, detail.variables) : draft.trim() ? [] : ['内容不能为空']
  const dirty = draft !== detail.active_content
  const busy = actions.publish.isPending || actions.activate.isPending || actions.reset.isPending
  const fail = (e: unknown) => toast({ tone: 'danger', title: '操作失败', message: e instanceof ApiError ? describeError(e.code) : undefined })

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
  const load = async (r: RevisionMeta) => setDraft((await fetchRevision(detail.kind, detail.key, r.id)).content)
  const compare = async (r: RevisionMeta) => setDiff({ title: `r${r.revision} 与当前生效版本（${detail.active_label}）`, lines: lineDiff((await fetchRevision(detail.kind, detail.key, r.id)).content, detail.active_content) })
  const seedActive = detail.revisions.every((r) => !r.is_active)

  return (
    <div className="cb-prompt-layout">
      <div className="cb-col">
        {list}
        <Panel title="历史版本" className={history ? undefined : 'cb-panel-folded'} actions={<Fold open={history} onToggle={onHistory} />}>
          {history ? (
            <ul className="cb-rev-list">
              {detail.revisions.map((r) => (
                <li key={r.id} data-active={r.is_active || undefined}>
                  <div className="cb-rev-head">
                    <span className="cb-mono">r{r.revision}</span>
                    {r.is_active ? <Badge tone="success" icon="check">生效中</Badge> : null}
                    <span className="cb-spacer" />
                    <span className="cb-muted">{formatTime(r.created_at)}</span>
                  </div>
                  <div className="cb-rev-note">{r.note || '未填写说明'}</div>
                  <div className="cb-rev-foot">
                    <span className="cb-muted">{r.author || '—'}</span>
                    <span className="cb-spacer" />
                    <Button size="sm" variant="ghost" onClick={() => void load(r)}>载入编辑</Button>
                    {r.is_active ? null : <Button size="sm" variant="ghost" onClick={() => void compare(r)}>对比</Button>}
                    {r.is_active ? null : <Button size="sm" variant="ghost" disabled={busy} onClick={() => actions.activate.mutate(r.id, { onSuccess: () => toast({ tone: 'success', title: `已切换到 r${r.revision}` }), onError: fail })}>启用</Button>}
                  </div>
                </li>
              ))}
              <li data-active={seedActive || undefined}>
                <div className="cb-rev-head">
                  <span className="cb-mono">{detail.seed_version}</span>
                  <Badge>内置</Badge>
                  {seedActive ? <Badge tone="success" icon="check">生效中</Badge> : null}
                </div>
                <div className="cb-rev-note">随程序发布，不可修改</div>
                <div className="cb-rev-foot">
                  <span className="cb-spacer" />
                  <Button size="sm" variant="ghost" onClick={() => setDraft(detail.seed_content)}>载入编辑</Button>
                  {seedActive ? null : <Button size="sm" variant="ghost" onClick={() => setDiff({ title: `内置 ${detail.seed_version} 与当前生效版本（${detail.active_label}）`, lines: lineDiff(detail.seed_content, detail.active_content) })}>对比</Button>}
                  {seedActive ? null : <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirmReset(true)}>启用</Button>}
                </div>
              </li>
            </ul>
          ) : null}
        </Panel>
      </div>

      <Panel
        title={detail.label}
        actions={
          <div className="cb-row">
            {dirty ? <Badge tone="warning">有未保存的修改</Badge> : null}
            <span className="cb-muted cb-nowrap">当前生效 <span className="cb-mono">{detail.active_label}</span></span>
            <Button variant="ghost" disabled={!dirty} onClick={() => setDiff({ title: `未保存的修改（对比 ${detail.active_label}）`, lines: lineDiff(detail.active_content, draft) })}>查看修改</Button>
            <Button variant="ghost" disabled={!dirty} onClick={() => setDraft(detail.active_content)}>放弃修改</Button>
            <Button variant="primary" disabled={busy || problems.length > 0 || !dirty} onClick={() => setSaving(true)}>保存</Button>
          </div>
        }
      >
        <div className="cb-col">
          <p className="cb-muted">{detail.description}</p>
          {problems.length ? <Callout tone="danger">{problems.join('；')}。</Callout> : null}
          <PromptEditor label="提示词内容" value={draft} onChange={setDraft} variables={detail.variables} />
        </div>
      </Panel>

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
            <div className="cb-diff" aria-label="差异">
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
