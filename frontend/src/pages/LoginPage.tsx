import { useState } from 'react'
import { fetchMe, login } from '../api/hooks'
import { ApiError } from '../lib/errors'
import { useAuth } from '../state/auth'
import { Button, Callout, Panel, TextField } from '../components/basic'

export function LoginPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const { setTokens, setUser } = useAuth()

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      setTokens(await login(username, password), username)
      setUser(await fetchMe())
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '登录失败，请稍后重试')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="cb-login">
      <Panel>
        <form
          className="cb-col"
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
        >
          <div className="cb-brand">
            <span className="cb-brand-mark">BI</span>ChatBI
          </div>
          <h1 className="title" style={{ margin: 0 }}>登录</h1>
          <TextField label="用户名" value={username} autoComplete="username" onChange={(e) => setUsername(e.target.value)} />
          <TextField label="密码" type="password" value={password} autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
          {error ? <Callout tone="danger">{error}</Callout> : null}
          <Button variant="primary" type="submit" disabled={busy || !username || !password}>
            登录
          </Button>
        </form>
      </Panel>
    </div>
  )
}
