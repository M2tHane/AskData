import { useEffect, useRef, useState } from 'react'
import { fetchMe, login } from '../api/hooks'
import { ApiError } from '../lib/errors'
import { useAuth } from '../state/auth'
import { Button, Callout } from '../components/basic'
import { Icon } from '../components/Icon'

const REMEMBER_KEY = 'askdata.login.username'
/** 请求过于频繁时主按钮暂停的时间。 */
const COOLDOWN_MS = 30_000

function readRemembered(): string {
  try {
    return localStorage.getItem(REMEMBER_KEY) ?? ''
  } catch {
    return ''
  }
}

/** 只记住账号，不存密码。 */
function writeRemembered(name: string | null) {
  try {
    if (name) localStorage.setItem(REMEMBER_KEY, name)
    else localStorage.removeItem(REMEMBER_KEY)
  } catch {
    /* storage blocked: nothing to remember */
  }
}

type LoginError = { tone: 'danger' | 'warning'; title: string; text?: string }

function describe(e: unknown): LoginError {
  if (e instanceof ApiError) {
    // 不区分「账号不存在」和「密码错」，不暴露账号是否存在。
    if (e.code === 'AUTH_FAILED') return { tone: 'danger', title: '账号或密码不正确', text: '检查大小写后重试。' }
    if (e.code === 'RATE_LIMITED') return { tone: 'warning', title: '尝试次数过多', text: '请 30 秒后再试；急用请联系团队负责人或管理员。' }
    return { tone: 'danger', title: '登录失败', text: e.message }
  }
  return { tone: 'danger', title: '连不上服务', text: '检查网络后重试。' }
}

export function LoginPage() {
  const remembered = readRemembered()
  const [username, setUsername] = useState(remembered)
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(remembered !== '')
  const [reveal, setReveal] = useState(false)
  const [caps, setCaps] = useState(false)
  const [error, setError] = useState<LoginError | null>(null)
  const [busy, setBusy] = useState(false)
  const [cooling, setCooling] = useState(false)
  const [tip, setTip] = useState(false)
  const { setTokens, setUser } = useAuth()
  const userRef = useRef<HTMLInputElement>(null)
  const passRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    // 记住了账号就直接聚焦密码。
    ;(remembered ? passRef : userRef).current?.focus()
  }, [remembered])

  useEffect(() => {
    if (!cooling) return
    const t = setTimeout(() => setCooling(false), COOLDOWN_MS)
    return () => clearTimeout(t)
  }, [cooling])

  const submit = async () => {
    if (busy || cooling) return
    setBusy(true)
    setError(null)
    try {
      setTokens(await login(username.trim(), password), username.trim())
      writeRemembered(remember ? username.trim() : null)
      setUser(await fetchMe())
    } catch (e) {
      const err = describe(e)
      setError(err)
      if (e instanceof ApiError && e.code === 'RATE_LIMITED') setCooling(true)
      passRef.current?.focus()
      passRef.current?.select()
    } finally {
      setBusy(false)
    }
  }

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => setCaps(e.getModifierState?.('CapsLock') ?? false)
  const passError = error?.tone === 'danger' && error.title === '账号或密码不正确'

  return (
    <div className="cb-login">
      <section className="cb-login-form">
        <div className="cb-brand">
          <span className="cb-mark" aria-hidden="true">
            A
          </span>
          AskData
        </div>
        <form
          className="cb-login-inner"
          noValidate
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
        >
          <div className="cb-login-title">
            <h1>登录 AskData</h1>
            <p>用公司账号登录，直接向数据提问。</p>
          </div>
          <div className="cb-field">
            <label htmlFor="login-user">账号</label>
            <div className="cb-input cb-input-lg">
              <input ref={userRef} id="login-user" name="username" autoComplete="username" placeholder="例如：lin.xiao" value={username} onChange={(e) => setUsername(e.target.value)} />
            </div>
          </div>
          <div className="cb-field" data-state={passError ? 'error' : caps ? 'warn' : undefined}>
            <label htmlFor="login-pass">
              <span>密码</span>
              <button type="button" className="cb-link cb-login-forgot" aria-expanded={tip} aria-controls="login-tip" onClick={() => setTip((v) => !v)}>
                忘记密码？
              </button>
            </label>
            <div className="cb-input cb-input-lg">
              <input
                ref={passRef}
                id="login-pass"
                name="password"
                type={reveal ? 'text' : 'password'}
                autoComplete="current-password"
                aria-invalid={passError || undefined}
                aria-describedby={caps ? 'login-caps' : undefined}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={onKey}
                onKeyUp={onKey}
              />
              <button type="button" className="cb-reveal" aria-pressed={reveal} aria-controls="login-pass" onClick={() => setReveal((v) => !v)}>
                {reveal ? '隐藏' : '显示'}
              </button>
            </div>
            {caps ? (
              <div className="cb-field-hint" id="login-caps">
                <Icon name="alert" size="sm" />
                大写锁定已打开
              </div>
            ) : null}
          </div>
          <label>
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
            记住账号
          </label>
          {error ? (
            <Callout tone={error.tone} title={error.title}>
              {error.text}
            </Callout>
          ) : null}
          <Button variant="primary" size="lg" block type="submit" disabled={busy || cooling || !username.trim() || !password}>
            {busy ? '登录中…' : '登录'}
          </Button>
          {tip ? (
            <p id="login-tip" className="cb-field-hint cb-login-tip">
              请联系你的团队负责人或管理员。
            </p>
          ) : null}
        </form>
        <div className="cb-login-foot">
          <span>© 2026 AskData</span>
          <span>内部系统 · 所有查询都会记录审计</span>
        </div>
      </section>
      <aside className="cb-login-show" aria-hidden="true">
        <h2>一句话查数，答案附带 SQL 与口径</h2>
        <div className="cb-login-demo">
          <div className="cb-q">近 6 个月线上渠道销售额走势？</div>
          <p className="cb-answer">
            9 月线上销售额 <b>¥1.86 亿</b>，同比增长 <b>24%</b>，连续 4 个月创新高。
          </p>
          <svg className="cb-login-chart" viewBox="0 0 300 96" preserveAspectRatio="none">
            {[38, 46, 52, 61, 70, 84].map((h, i) => (
              <rect key={i} className="cb-s1" x={14 + i * 48} y={96 - h} width={24} height={h} rx={2} />
            ))}
          </svg>
          <div className="cb-sql">
            <pre>
              <span className="cb-kw">SELECT</span> month, <span className="cb-kw">SUM</span>(amount) <span className="cb-kw">FROM</span> dw.orders <span className="cb-kw">WHERE</span> channel = <span className="cb-str">'online'</span> …
            </pre>
          </div>
        </div>
        <ul>
          <li>
            <Icon name="check" />
            只读 SQL，执行前自动校验
          </li>
          <li>
            <Icon name="check" />
            按团队隔离数据权限，敏感字段脱敏
          </li>
          <li>
            <Icon name="check" />
            每次查询都能追溯到问题、SQL 和结果
          </li>
        </ul>
      </aside>
    </div>
  )
}
