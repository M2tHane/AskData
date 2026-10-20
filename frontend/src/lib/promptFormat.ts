import { parseDocument } from 'yaml'

/** 提示词的两种文本格式；由内容自动识别，不需要用户选择。 */
export type PromptFormat = 'markdown' | 'yaml'

export const FORMAT_LABEL: Record<PromptFormat, string> = { markdown: 'Markdown', yaml: 'YAML' }

const YAML_KEY = /^[A-Za-z_][\w.-]*:(\s|$)/
const MD_HEADING = /^#{2,6}\s/m

/**
 * 按内容识别格式：第一条非空、非 # 开头的行是顶格的 `key:`（或文档以 --- 开头）就是 YAML；
 * 出现二级及以下的 Markdown 标题则一律按 Markdown。写到一半的 YAML 也不会因为语法错误而被当成 Markdown。
 */
export function detectFormat(text: string): PromptFormat {
  if (MD_HEADING.test(text)) return 'markdown'
  for (const raw of text.split('\n')) {
    const line = raw.trimEnd()
    if (!line.trim() || line.startsWith('#')) continue
    return line === '---' || YAML_KEY.test(line) ? 'yaml' : 'markdown'
  }
  return 'markdown'
}

export type TokenKind = 'plain' | 'head' | 'list' | 'bold' | 'code' | 'key' | 'string' | 'number' | 'comment' | 'var' | 'tag'

export interface Token {
  text: string
  kind: TokenKind
  /** kind 为 var 时是变量名（不含 $ 和花括号）。 */
  name?: string
}

const INLINE_SPECIAL = /(\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*|<\/?untrusted_data>)/g

/** 把一段文字里的 ${变量} 和 <untrusted_data> 标签拆出来，其余保持原 kind。 */
function splitSpecial(text: string, kind: TokenKind): Token[] {
  if (!text) return []
  const out: Token[] = []
  let last = 0
  for (const m of text.matchAll(INLINE_SPECIAL)) {
    const at = m.index ?? 0
    if (at > last) out.push({ text: text.slice(last, at), kind })
    const hit = m[0]
    if (hit.startsWith('<')) out.push({ text: hit, kind: 'tag' })
    else out.push({ text: hit, kind: 'var', name: hit.replace(/^\$\{?|\}$/g, '') })
    last = at + hit.length
  }
  if (last < text.length) out.push({ text: text.slice(last), kind })
  return out
}

const MD_INLINE = /(\*\*[^*\n]+\*\*|`[^`\n]+`)/g

function markdownInline(text: string): Token[] {
  const out: Token[] = []
  let last = 0
  for (const m of text.matchAll(MD_INLINE)) {
    const at = m.index ?? 0
    out.push(...splitSpecial(text.slice(last, at), 'plain'))
    out.push(...splitSpecial(m[0], m[0].startsWith('`') ? 'code' : 'bold'))
    last = at + m[0].length
  }
  out.push(...splitSpecial(text.slice(last), 'plain'))
  return out
}

function markdownLine(line: string): Token[] {
  if (/^#{1,6} /.test(line)) return splitSpecial(line, 'head')
  const list = /^(\s*)([-*+]|\d+\.)(\s+)(.*)$/.exec(line)
  if (list) {
    const [, indent = '', marker = '', gap = '', rest = ''] = list
    return [...splitSpecial(indent, 'plain'), { text: marker, kind: 'list' }, ...splitSpecial(gap, 'plain'), ...markdownInline(rest)]
  }
  return markdownInline(line)
}

function yamlScalar(value: string): Token[] {
  const body = value.trim()
  if (!body) return splitSpecial(value, 'plain')
  const comment = /^(.*?)(\s+#.*)$/.exec(value)
  if (comment && !/^["']/.test(body)) return [...yamlScalar(comment[1] ?? ''), ...splitSpecial(comment[2] ?? '', 'comment')]
  if (/^-?\d+(\.\d+)?$/.test(body) || /^(true|false|null|yes|no)$/i.test(body)) return splitSpecial(value, 'number')
  if (/^(".*"|'.*')$/.test(body)) return splitSpecial(value, 'string')
  return splitSpecial(value, 'plain')
}

const YAML_ENTRY = /^(\s*)((?:-\s+)*)([A-Za-z_][\w.-]*)(:)(\s+(.*)|\s*)$/
const BLOCK_START = /^[|>][+-]?\d*\s*$/

function yamlTokens(lines: string[]): Token[][] {
  let block: number | null = null // 块标量（| 或 >）内容的缩进下限；null 表示不在块里
  return lines.map((line) => {
    const indent = line.length - line.trimStart().length
    if (block !== null) {
      if (!line.trim() || indent >= block) return splitSpecial(line, 'plain')
      block = null
    }
    if (/^\s*#/.test(line)) return splitSpecial(line, 'comment')
    const m = YAML_ENTRY.exec(line)
    if (m) {
      const [, lead = '', dashes = '', key = '', colon = ':', tail = '', value = ''] = m
      if (BLOCK_START.test(value)) block = lead.length + dashes.length + 1
      const gap = tail.slice(0, tail.length - value.length)
      return [...splitSpecial(lead + dashes, 'plain'), { text: key, kind: 'key' as const }, { text: colon, kind: 'plain' as const }, ...splitSpecial(gap, 'plain'), ...(BLOCK_START.test(value) ? [{ text: value, kind: 'plain' as const }] : yamlScalar(value))]
    }
    const item = /^(\s*-\s+)(.*)$/.exec(line)
    if (item) return [...splitSpecial(item[1] ?? '', 'plain'), ...yamlScalar(item[2] ?? '')]
    return splitSpecial(line, 'plain')
  })
}

/** 把整段文本切成带类型的片段；所有片段的 text 拼起来与原文逐字相同（编辑器背景层依赖这一点）。 */
export function tokenize(text: string, format: PromptFormat = detectFormat(text)): Token[] {
  const lines = text.split('\n')
  const perLine = format === 'yaml' ? yamlTokens(lines) : lines.map(markdownLine)
  const out: Token[] = []
  perLine.forEach((tokens, i) => {
    out.push(...tokens)
    if (i < lines.length - 1) out.push({ text: '\n', kind: 'plain' })
  })
  return out
}

export interface FormatIssue {
  line: number
  message: string
}

const YAML_ERRORS: Record<string, string> = {
  BAD_INDENT: '缩进不正确',
  TAB_AS_INDENT: '不能用制表符缩进',
  DUPLICATE_KEY: '键重复',
  MISSING_CHAR: '缺少必要的符号',
  BAD_SCALAR_START: '值不能以这个符号开头',
  BLOCK_AS_IMPLICIT_KEY: '键必须写在同一行',
  MULTILINE_IMPLICIT_KEY: '键必须写在同一行',
  UNEXPECTED_TOKEN: '出现了多余的内容',
  BAD_ALIAS: '引用名无效',
}

/** 格式校验：YAML 解析错误会带行号；Markdown 没有语法错误。 */
export function validateFormat(text: string, format: PromptFormat): FormatIssue | null {
  if (format !== 'yaml') return null
  const err = parseDocument(text).errors[0]
  if (!err) return null
  return { line: err.linePos?.[0].line ?? 1, message: YAML_ERRORS[err.code] ?? '语法错误' }
}

/** 整理 YAML：解析后按 2 空格缩进重新输出，保留注释、块标量和全部文字；有语法错误时返回 null。 */
export function formatYaml(text: string): string | null {
  const doc = parseDocument(text)
  if (doc.errors.length) return null
  return doc.toString({ lineWidth: 0, indent: 2 })
}

/**
 * 保守整理 Markdown：去掉行尾空格（保留强制换行的两个空格）、列表缩进里的制表符换成两个空格、
 * 连续空行压成一行、去掉首尾空行。代码围栏里的内容原样保留。
 */
export function formatMarkdown(text: string): string {
  let fenced = false
  const lines = text.split('\n').map((raw) => {
    if (/^\s*```/.test(raw)) {
      fenced = !fenced
      return raw.trimEnd()
    }
    if (fenced) return raw
    let line = raw
    if (/^\t+([-*+]|\d+\.)\s/.test(line)) line = line.replace(/^\t+/, (t) => '  '.repeat(t.length))
    const hard = / {2,}$/.test(line) && line.trim() !== ''
    line = line.trimEnd()
    return hard && !/^\s*$/.test(line) && !line.startsWith('#') ? `${line}  ` : line
  })
  const out: string[] = []
  fenced = false
  for (const line of lines) {
    if (/^\s*```/.test(line)) fenced = !fenced
    if (!fenced && line === '' && (out.length === 0 || out[out.length - 1] === '')) continue
    out.push(line)
  }
  while (out.length && out[out.length - 1] === '') out.pop()
  return out.join('\n') + (text.endsWith('\n') ? '\n' : '')
}

/** 按格式整理；YAML 有语法错误时返回 null（先修好错误）。 */
export function formatPrompt(text: string, format: PromptFormat): string | null {
  return format === 'yaml' ? formatYaml(text) : formatMarkdown(text)
}
