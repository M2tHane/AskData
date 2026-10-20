import { describe, expect, it } from 'vitest'
import type { VisibilityRead } from '../api/types'
import { fromRead, inheritTable, isCustom, resetAll, resolve, sameState, setColumnTeams, setDatasourceTeams, setTableTeams, toWrite } from './visibility'

const read: VisibilityRead = {
  datasource_id: 1,
  team_ids: [1, 2],
  custom: false,
  tables: [{ table_id: 10, custom: false, team_ids: [1, 2], columns: [{ column_id: 100, custom: false, team_ids: [1, 2] }, { column_id: 101, custom: false, team_ids: [1, 2] }] }],
}

describe('visibility draft', () => {
  it('is not dirty until something changes and stays inherited by default', () => {
    const s = fromRead(read)
    expect(isCustom(s)).toBe(false)
    expect(sameState(s, fromRead(read))).toBe(true)
    expect(toWrite(s).tables).toEqual([])
  })
  it('inherits through levels and clips custom objects to their parent', () => {
    let s = setTableTeams(fromRead(read), 10, [2])
    s = setColumnTeams(s, 10, 101, [1, 2])
    expect(resolve(s).tables[10]).toEqual([2])
    expect(resolve(s).columns).toEqual({ 100: [2], 101: [2] })
    s = setDatasourceTeams(s, [1])
    expect(resolve(s).tables[10]).toEqual([])
    expect(isCustom(s)).toBe(true)
  })
  it('adding a team to the datasource does not widen customized tables', () => {
    let s = setTableTeams(fromRead(read), 10, [1])
    s = setDatasourceTeams(s, [1, 2, 3])
    expect(resolve(s).tables[10]).toEqual([1])
  })
  it('serializes only customized objects, using effective teams', () => {
    let s = setColumnTeams(fromRead(read), 10, 100, [1])
    expect(toWrite(s)).toEqual({ team_ids: [1, 2], tables: [{ table_id: 10, custom: false, team_ids: [], columns: [{ column_id: 100, team_ids: [1] }] }] })
    s = inheritTable(setTableTeams(s, 10, [2]), 10)
    expect(isCustom(s)).toBe(false)
    expect(isCustom(resetAll(setTableTeams(s, 10, [2])))).toBe(false)
  })
})
