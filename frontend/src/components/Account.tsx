import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '@/api/client'
import type { User } from '@/api/types'
import { errorMessage } from '@/api/ui'
import { Button } from '@/components/arc/button/button'

export function Account({ onLogin }: { onLogin: (user: User) => void }) {
  const [register, setRegister] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const credentials = { email: String(data.get('email')).trim(), password: String(data.get('password')) }
    setBusy(true)
    setError('')
    try {
      onLogin(register
        ? await api.register({ ...credentials, display_name: String(data.get('name')).trim() })
        : await api.login(credentials))
    } catch (error) {
      setError(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="stack-form" onSubmit={submit}>
      {register && <label>称呼<input name="name" autoComplete="nickname" required maxLength={100} /></label>}
      <label>邮箱<input name="email" type="email" autoComplete="username" required maxLength={254} /></label>
      <label>密码<input name="password" type="password" minLength={8} maxLength={128} autoComplete={register ? 'new-password' : 'current-password'} required /></label>
      {error && <p role="alert" className="error-text">{error}</p>}
      <div className="form-actions">
        <Button type="submit" loading={busy}>{register ? '创建账号' : '登录'}</Button>
        <Button type="button" variant="ghost" disabled={busy} onClick={() => { setRegister(!register); setError('') }}>
          {register ? '已有账号，去登录' : '注册账号'}
        </Button>
      </div>
    </form>
  )
}
