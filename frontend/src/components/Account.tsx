import { useEffect, useId, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { BookOpen } from 'lucide-react'
import { api } from '@/api/client'
import { ApiError } from '@/api/stream'
import type { User } from '@/api/types'
import { errorMessage } from '@/api/ui'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { Input } from '@/components/arc/input/input'
import { PasswordField } from '@/components/arc/password-field/password-field'
import styles from './Account.module.css'

export type AccountMode = 'login' | 'register' | 'forgot' | 'reset' | 'google_link'
const titles = { login: '欢迎回来', register: '创建账号', forgot: '找回密码', reset: '设置新密码', google_link: '绑定 Google 账号' }
const descriptions = {
  login: '登录知循，继续你的资料问答',
  register: '创建你的个人知识空间',
  forgot: '输入账号邮箱，接收密码重置链接',
  reset: '设置新密码后，请重新登录',
  google_link: '输入知循密码，确认绑定此 Google 账号',
}

export function Account({ open, onOpenChange, onLogin, mode, setMode, next, resetToken, initialError, returnFocus }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onLogin: (user: User) => void
  mode: AccountMode
  setMode: (mode: AccountMode) => void
  next: string
  resetToken: string
  initialError: string
  returnFocus: () => void
}) {
  const [options, setOptions] = useState<{ google: boolean; password_reset: boolean } | null>(null)
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ message: string; field?: 'email' | 'password' } | null>(null)
  const [notice, setNotice] = useState('')
  const passwordError = useId()
  const formRef = useRef<HTMLFormElement>(null)
  const submitting = useRef(false)
  const showEmail = mode === 'login' || mode === 'register' || mode === 'forgot'
  const newPassword = mode === 'register' || mode === 'reset'

  useEffect(() => {
    if (!open) {
      setError(null)
      setNotice('')
      return
    }
    let current = true
    void api.authOptions().then((value) => { if (current) setOptions(value) }, () => { if (current) setOptions(null) })
    return () => { current = false }
  }, [open])

  function changeMode(nextMode: AccountMode) {
    setError(null)
    setNotice('')
    setMode(nextMode)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting.current) return
    const data = new FormData(event.currentTarget)
    const credentials = { email: email.trim(), password: String(data.get('password') ?? '') }
    if (mode === 'reset' && credentials.password !== data.get('confirmation')) {
      setError({ message: '两次输入的密码不一致' })
      return
    }
    submitting.current = true
    setBusy(true)
    setError(null)
    setNotice('')
    try {
      if (mode === 'forgot') {
        setNotice((await api.forgotPassword(credentials.email)).message)
      } else if (mode === 'reset') {
        await api.resetPassword(resetToken, credentials.password)
        changeMode('login')
        setNotice('密码已重置，请使用新密码登录。')
      } else {
        onLogin(mode === 'google_link' ? await api.googleLink(credentials.password)
          : mode === 'register' ? await api.register({ ...credentials, display_name: String(data.get('name')).trim() })
            : await api.login(credentials))
      }
    } catch (failure) {
      const code = failure instanceof ApiError ? failure.code : ''
      const field = code === 'invalid_account' ? 'email' : code === 'invalid_password' ? 'password' : undefined
      setError({ message: errorMessage(failure), field })
      if (field) formRef.current?.querySelector<HTMLInputElement>('[name="' + field + '"]')?.focus()
    } finally {
      submitting.current = false
      setBusy(false)
    }
  }

  return <Dialog open={open} onOpenChange={(value) => { if (!busy) onOpenChange(value) }}>
    <DialogContent className={styles.card} overlayClassName={styles.overlay} title={titles[mode]} description={descriptions[mode]}
      headerStart={<div className={styles.brand}><BookOpen size={24} aria-hidden="true" /><strong>知循</strong><span>KnowLoop</span></div>}
      closeLabel="关闭登录窗口"
      onOpenAutoFocus={(event) => {
        const field = formRef.current?.querySelector<HTMLInputElement>('input')
        if (field) { event.preventDefault(); field.focus() }
      }}
      onCloseAutoFocus={(event) => { event.preventDefault(); returnFocus() }}>
      <form ref={formRef} key={mode} className={styles.form} onSubmit={submit} aria-busy={busy}>
        {mode === 'register' && <Input label="称呼" name="name" autoComplete="nickname" required maxLength={100} />}
        {showEmail && <Input label="邮箱地址" name="email" type="email" placeholder="输入邮箱地址" autoComplete="username" required maxLength={254} value={email} onChange={(event) => setEmail(event.target.value)}
          error={error?.field === 'email' ? error.message : undefined} />}
        {mode !== 'forgot' && <PasswordField label={mode === 'reset' ? '新密码' : '密码'} name="password" placeholder="输入密码"
          autoComplete={newPassword ? 'new-password' : 'current-password'} required minLength={8} maxLength={128}
          messages={{ showPassword: '显示密码', hidePassword: '隐藏密码' }}
          aria-invalid={error?.field === 'password' || undefined} aria-describedby={error?.field === 'password' ? passwordError : undefined} />}
        {error?.field === 'password' && <p id={passwordError} className="error-text" role="alert">{error.message}</p>}
        {mode === 'reset' && <PasswordField label="确认新密码" name="confirmation" autoComplete="new-password" required minLength={8} maxLength={128}
          messages={{ showPassword: '显示确认密码', hidePassword: '隐藏确认密码' }} />}
        {mode === 'login' && <div className={styles.forgot}>
          <Button type="button" variant="ghost" size="sm" disabled={busy || !options?.password_reset} onClick={() => changeMode('forgot')}>{options && !options.password_reset ? '邮件找回暂不可用' : '忘记密码？'}</Button>
        </div>}
        {(error && !error.field || initialError && !notice && !error) && <p role="alert" className="error-text">{error?.message ?? initialError}</p>}
        {notice && <p role="status">{notice}</p>}
        <Button className={styles.submit} type="submit" loading={busy}>{({ login: '登录', register: '创建账号', forgot: '发送重置邮件', reset: '设置新密码', google_link: '验证并绑定' })[mode]}</Button>
        {mode === 'login' && <>
          <div className={styles.divider}>或</div>
          <Button type="button" variant="secondary" disabled={busy || !options?.google} onClick={() => window.location.assign('/api/v1/auth/google/start?' + new URLSearchParams({ next }))}>{options && !options.google ? 'Google 登录暂不可用' : '使用 Google 继续'}</Button>
          <div className={styles.footer}><span>还没有账号？</span><Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => changeMode('register')}>立即注册</Button></div>
        </>}
        {mode !== 'login' && <div className={styles.footer}><Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => changeMode('login')}>返回登录</Button></div>}
      </form>
    </DialogContent>
  </Dialog>
}
