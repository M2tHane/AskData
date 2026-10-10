import type { ScopeLevel, SemanticView } from '../api/types'
import { UPPER_LEVELS } from './scope'

type EnumValue = string | number | boolean | null
export type EnumMap = Record<string, EnumValue>

/** 一行（表或字段）在编辑器里的内容。 */
export interface SemanticDraft {
  alias: string
  description: string
  enumOn: boolean
  enums: { key: string; value: string }[]
}

export const splitAliases = (text: string) => text.split(/[、,，\s]+/).filter(Boolean)

export function toDraft(view?: SemanticView): SemanticDraft {
  const enums = Object.entries(view?.enum_mapping ?? {}).map(([key, value]) => ({ key, value: value == null ? '' : String(value) }))
  return { alias: (view?.aliases ?? []).join('、'), description: view?.description ?? '', enumOn: enums.length > 0, enums }
}

/** 去掉空白和空键，便于比较「有没有改」和判断「是不是空的」。 */
export function normalize(d: SemanticDraft) {
  return {
    aliases: splitAliases(d.alias),
    description: d.description.trim(),
    enums: d.enumOn ? d.enums.filter((e) => e.key.trim() !== '').map((e) => ({ key: e.key.trim(), value: e.value.trim() })) : [],
  }
}

export const sameDraft = (a: SemanticDraft, b: SemanticDraft) => JSON.stringify(normalize(a)) === JSON.stringify(normalize(b))

export function isEmptyDraft(d: SemanticDraft) {
  const n = normalize(d)
  return n.aliases.length === 0 && n.description === '' && n.enums.length === 0
}

/** 写入后端的内容；取值保持原来的类型（数字不被变成字符串）。 */
export function draftBody(d: SemanticDraft, original?: EnumMap) {
  const n = normalize(d)
  const enum_mapping: EnumMap = {}
  for (const { key, value } of n.enums) {
    const old = original?.[key]
    enum_mapping[key] = old != null && String(old) === value ? old : value
  }
  return { aliases: n.aliases, description: n.description, enum_mapping }
}

export interface LevelView {
  /** 本级（角色决定的那一级）已有的定义。 */
  own?: SemanticView
  /** 本级没有定义时，生效的上一级定义。 */
  upper?: SemanticView
  /** 编辑的起点：本级定义，没有就是上一级当前的值。 */
  base?: SemanticView
}

/** 在一个目标的全部可见定义里，找出本级和继承来的那一条。 */
export function levelView(items: SemanticView[], level: ScopeLevel): LevelView {
  const own = items.find((s) => s.scope === level)
  const upper = UPPER_LEVELS[level].map((l) => items.find((s) => s.scope === l)).find(Boolean)
  return { own, upper, base: own ?? upper }
}

export type SourceKind = { kind: 'own' } | { kind: 'inherit'; from: ScopeLevel } | { kind: 'none' }

export function sourceOf(v: LevelView): SourceKind {
  if (v.own) return { kind: 'own' }
  return v.upper ? { kind: 'inherit', from: v.upper.scope } : { kind: 'none' }
}

export type SaveStep = { op: 'create' | 'update' | 'delete' | 'skip'; id?: number }

/** 本行的保存动作：改成全空就删除本级定义；本级没有定义的新建；已有的更新。 */
export function planStep(draft: SemanticDraft, view: LevelView): SaveStep {
  if (isEmptyDraft(draft)) return view.own ? { op: 'delete', id: view.own.id } : { op: 'skip' }
  return view.own ? { op: 'update', id: view.own.id } : { op: 'create' }
}
