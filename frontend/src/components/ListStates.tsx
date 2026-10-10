import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Button } from './basic'
import { Icon, type IconName } from './Icon'
import { ApiError, friendlyError } from '../lib/errors'

/** 首次为空：图标 + 这里会有什么 + 一个主操作（只给有权限新建的角色）。 */
export function EmptyState(props: { icon?: IconName; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="cb-state">
      <span className="cb-state-ic" aria-hidden="true">
        <Icon name={props.icon ?? 'table'} />
      </span>
      <b>{props.title}</b>
      {props.children ? <p>{props.children}</p> : null}
      {props.action ? <div className="cb-row">{props.action}</div> : null}
    </div>
  )
}

/** 搜索无结果：复述关键词，给「清空搜索」。 */
export function NoResults(props: { query?: string; onClear?: () => void; children?: ReactNode }) {
  return (
    <div className="cb-state">
      <span className="cb-state-ic" aria-hidden="true">
        <Icon name="search" />
      </span>
      <b>{props.query ? `没有找到「${props.query}」` : '没有符合条件的结果'}</b>
      <p>{props.children ?? '换个关键词，或调整筛选条件。'}</p>
      {props.onClear ? (
        <div className="cb-row">
          <Button onClick={props.onClear}>{props.query ? '清空搜索' : '清空筛选'}</Button>
        </div>
      ) : null}
    </div>
  )
}

/** 加载中：超过 300ms 才出现骨架行，避免一闪而过。 */
export function SkeletonRows(props: { rows?: number; cols?: number }) {
  const [show, setShow] = useState(false)
  useEffect(() => {
    const t = setTimeout(() => setShow(true), 300)
    return () => clearTimeout(t)
  }, [])
  if (!show) return null
  const cols = props.cols ?? 4
  return (
    <div className="cb-skel-rows" aria-busy="true" aria-label="加载中">
      {Array.from({ length: props.rows ?? 5 }, (_, r) => (
        <div key={r} className="cb-skel-row">
          {Array.from({ length: cols }, (_, c) => (
            <span key={c} className="cb-skel" style={{ flex: c === 0 ? 2 : 1, maxWidth: c === cols - 1 ? '20%' : undefined }} />
          ))}
        </div>
      ))}
    </div>
  )
}

/** 加载失败：说清是哪个服务、数据有没有丢，给「重试」和错误编号。 */
export function LoadError(props: { error: unknown; what?: string; onRetry?: () => void }) {
  const code = props.error instanceof ApiError ? props.error.code : 'INTERNAL_ERROR'
  const requestId = props.error instanceof ApiError ? props.error.requestId : undefined
  const network = props.error instanceof TypeError
  const f = friendlyError(code)
  return (
    <div className="cb-state cb-state-danger" role="alert">
      <span className="cb-state-ic" aria-hidden="true">
        <Icon name="alert" />
      </span>
      <b>{props.what ? `${props.what}加载失败` : '加载失败'}</b>
      <p>{network ? '连不上服务，检查网络后重试。已有数据不受影响。' : `${f.title}。${f.hint ?? '已有数据不受影响。'}`}</p>
      {props.onRetry ? (
        <div className="cb-row">
          <Button onClick={props.onRetry} icon="refresh">
            重试
          </Button>
        </div>
      ) : null}
      <span className="cb-state-code">
        错误编号 {requestId ? `${code} · ${requestId.slice(0, 8)}` : code}
      </span>
    </div>
  )
}

/**
 * 列表的四种非正常状态，按优先级挑一种显示；都不是时渲染列表本身。
 * filtered 表示当前有搜索或筛选：空列表时显示「无结果」而不是「首次为空」。
 */
export function ListStatus(props: {
  loading: boolean
  error?: unknown
  onRetry?: () => void
  what?: string
  count: number
  filtered?: boolean
  query?: string
  onClear?: () => void
  empty: ReactNode
  cols?: number
  children: ReactNode
}) {
  if (props.error) return <LoadError error={props.error} what={props.what} onRetry={props.onRetry} />
  if (props.loading) return <SkeletonRows cols={props.cols} />
  if (props.count === 0) return props.filtered ? <NoResults query={props.query} onClear={props.onClear} /> : <>{props.empty}</>
  return <>{props.children}</>
}
