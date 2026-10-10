import { describe, expect, it } from 'vitest'
import type { SemanticView } from '../api/types'
import { draftBody, isEmptyDraft, levelView, planStep, sameDraft, sourceOf, toDraft } from './semanticLevels'

const v = (id: number, scope: SemanticView['scope'], extra: Partial<SemanticView> = {}) => ({ id, scope, target_type: 'column', target_id: 1, aliases: [], description: '', ...extra }) as SemanticView

describe('semantic levels', () => {
  it('a member starts from the team value, then the platform value', () => {
    const view = levelView([v(1, 'platform'), v(2, 'team')], 'personal')
    expect(view.own).toBeUndefined()
    expect(view.base?.id).toBe(2)
    expect(sourceOf(view)).toEqual({ kind: 'inherit', from: 'team' })
    expect(sourceOf(levelView([v(1, 'platform')], 'personal'))).toEqual({ kind: 'inherit', from: 'platform' })
    expect(sourceOf(levelView([], 'personal'))).toEqual({ kind: 'none' })
  })
  it('an own record wins, and an admin never inherits', () => {
    const view = levelView([v(1, 'platform'), v(3, 'personal')], 'personal')
    expect(sourceOf(view)).toEqual({ kind: 'own' })
    expect(view.upper?.id).toBe(1)
    expect(levelView([v(1, 'platform'), v(2, 'team')], 'platform').upper).toBeUndefined()
  })
  it('plans saves: delete when emptied, create or update otherwise', () => {
    const own = levelView([v(3, 'personal', { aliases: ['销售额'] })], 'personal')
    expect(planStep(toDraft(), own)).toEqual({ op: 'delete', id: 3 })
    expect(planStep(toDraft(), levelView([], 'personal'))).toEqual({ op: 'skip' })
    const edited = { ...toDraft(), alias: '营收' }
    expect(planStep(edited, own)).toEqual({ op: 'update', id: 3 })
    expect(planStep(edited, levelView([], 'personal'))).toEqual({ op: 'create' })
  })
  it('enum rows ignore blank keys and keep original value types', () => {
    const d = { alias: '', description: '', enumOn: true, enums: [{ key: '1', value: '线上' }, { key: ' ', value: 'x' }] }
    expect(isEmptyDraft(d)).toBe(false)
    expect(draftBody(d, { '1': '线上' }).enum_mapping).toEqual({ '1': '线上' })
    expect(draftBody({ ...d, enums: [{ key: '1', value: '2' }] }, { '1': 2 }).enum_mapping).toEqual({ '1': 2 })
    expect(sameDraft(d, { ...d, enums: [d.enums[0]!] })).toBe(true)
    expect(isEmptyDraft({ ...d, enumOn: false })).toBe(true)
  })
})
