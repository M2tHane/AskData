import { describe, expect, it } from 'vitest'
import { changed, lineDiff, templateProblems, usedVariables } from './diff'

describe('lineDiff', () => {
  it('marks added and removed lines and keeps the common ones', () => {
    const lines = lineDiff('a\nb\nc', 'a\nx\nc\nd')
    expect(lines.map((l) => `${l.kind}:${l.text}`)).toEqual(['same:a', 'del:b', 'add:x', 'same:c', 'add:d'])
    expect(changed(lines)).toBe(3)
    expect(changed(lineDiff('same', 'same'))).toBe(0)
  })
})

describe('templateProblems', () => {
  const block = '<untrusted_data>$data</untrusted_data>'
  it('accepts exactly the registered variables with the data block', () => {
    expect(usedVariables(`Use $engine. $dialect ${block}`)).toEqual(['data', 'dialect', 'engine'])
    expect(templateProblems(`Use $engine. $dialect ${block}`, ['data', 'dialect', 'engine'])).toEqual([])
  })
  it('reports missing, extra and unprotected data', () => {
    expect(templateProblems('$engine $extra $data', ['data', 'dialect', 'engine'])).toEqual([
      '缺少变量 $dialect',
      '不能使用未登记的变量 $extra',
      '必须保留 <untrusted_data>$data</untrusted_data> 数据块',
    ])
    expect(templateProblems('', ['data'])).toContain('内容不能为空')
  })
})
