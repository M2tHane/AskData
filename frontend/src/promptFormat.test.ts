import { describe, expect, it } from 'vitest'
import { detectFormat, formatMarkdown, formatPrompt, formatYaml, tokenize, validateFormat } from './lib/promptFormat'

const YAML = 'name: sql\nversion: v8\nvariables:\n  - data\ntemplate: |\n  Rules:\n  1. use ${data}\n'
const MD = '# 角色\n你是**分析助手**。\n\n## 规则\n1. 只输出 `SELECT`\n- 用 ${question}\n'

describe('detectFormat', () => {
  it('recognizes yaml and markdown', () => {
    expect(detectFormat(YAML)).toBe('yaml')
    expect(detectFormat('# 注释\nname: x')).toBe('yaml')
    expect(detectFormat(MD)).toBe('markdown')
    expect(detectFormat('')).toBe('markdown')
    expect(detectFormat('只输出 JSON\n<untrusted_data>$data</untrusted_data>')).toBe('markdown')
  })
  it('keeps broken yaml as yaml', () => {
    expect(detectFormat('name: x\n  bad: [')).toBe('yaml')
  })
})

describe('tokenize', () => {
  it('round-trips the text exactly', () => {
    for (const t of [YAML, MD, '', '\n\n', 'a: "x" # c\nb: 12\n- c: 1']) {
      expect(tokenize(t).map((x) => x.text).join('')).toBe(t)
    }
  })
  it('marks markdown tokens', () => {
    const kinds = tokenize(MD, 'markdown')
    expect(kinds.find((t) => t.kind === 'head')?.text).toBe('# 角色')
    expect(kinds.find((t) => t.kind === 'bold')?.text).toBe('**分析助手**')
    expect(kinds.find((t) => t.kind === 'code')?.text).toBe('`SELECT`')
    expect(kinds.filter((t) => t.kind === 'list').map((t) => t.text)).toEqual(['1.', '-'])
    expect(kinds.find((t) => t.kind === 'var')?.name).toBe('question')
  })
  it('marks yaml tokens and does not treat block scalar text as keys', () => {
    const toks = tokenize('a: 12\nb: "x"\n# note\nt: |\n  Rules: ${data}\n', 'yaml')
    expect(toks.filter((t) => t.kind === 'key').map((t) => t.text)).toEqual(['a', 'b', 't'])
    expect(toks.find((t) => t.kind === 'number')?.text).toBe(' 12'.trim() === '12' ? '12' : '')
    expect(toks.find((t) => t.kind === 'string')?.text).toBe('"x"')
    expect(toks.find((t) => t.kind === 'comment')?.text).toBe('# note')
    expect(toks.find((t) => t.kind === 'var')?.name).toBe('data')
  })
  it('marks untrusted_data tags in both formats', () => {
    const text = '<untrusted_data>$data</untrusted_data>'
    expect(tokenize(text, 'markdown').filter((t) => t.kind === 'tag')).toHaveLength(2)
    expect(tokenize(`a: |\n  ${text}`, 'yaml').filter((t) => t.kind === 'tag')).toHaveLength(2)
  })
})

describe('validateFormat', () => {
  it('reports the yaml error line', () => {
    expect(validateFormat(YAML, 'yaml')).toBeNull()
    const issue = validateFormat('a: 1\nb: [1, 2\nc: 3\n', 'yaml')
    expect(issue?.line).toBeGreaterThanOrEqual(2)
    expect(validateFormat('a: 1\n\tb: 2\n', 'yaml')?.line).toBe(2)
  })
  it('never fails markdown', () => {
    expect(validateFormat('a: [', 'markdown')).toBeNull()
  })
})

describe('format', () => {
  it('formats yaml without changing content', () => {
    const out = formatYaml('name:   sql\nvariables:\n    - data\ntemplate: |\n    use ${data}\n    next\n')
    expect(out).toBe('name: sql\nvariables:\n  - data\ntemplate: |\n  use ${data}\n  next\n')
  })
  it('refuses to format invalid yaml', () => {
    expect(formatYaml('a: [1')).toBeNull()
    expect(formatPrompt('a: [1', 'yaml')).toBeNull()
  })
  it('formats markdown conservatively', () => {
    expect(formatMarkdown('\n\n# 标题  \n\n\n\n\t- 子项   \n正文  \n')).toBe('# 标题\n\n  - 子项  \n正文  \n')
    expect(formatMarkdown('```\n  keep   \n\n\n```\nend')).toBe('```\n  keep   \n\n\n```\nend')
    const text = '## a\n1. x\n'
    expect(formatMarkdown(text)).toBe(text)
  })
})
