import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { authApi, setSession } from '../api/client.js'

export default function RegisterPage() {
  const navigate = useNavigate()
  const [form, setForm] = useState({ username: '', email: '', password: '' })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  function update(key, value) {
    setForm((old) => ({ ...old, [key]: value }))
  }

  async function submit(e) {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const data = await authApi.register(form)
      setSession(data.access_token, data.user)
      navigate('/')
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={submit}>
        <div className="docs-logo">▣</div>
        <h1>Tạo tài khoản</h1>
        <p>Tài khoản dùng để quản lý tài liệu, phân quyền và ghi lịch sử chỉnh sửa.</p>
        <input value={form.username} onChange={(e) => update('username', e.target.value)} placeholder="Tên hiển thị" required />
        <input value={form.email} onChange={(e) => update('email', e.target.value)} type="email" placeholder="Email" required />
        <input value={form.password} onChange={(e) => update('password', e.target.value)} type="password" placeholder="Mật khẩu tối thiểu 6 ký tự" required />
        {error && <div className="error-box">{error}</div>}
        <button className="primary-button" disabled={loading}>{loading ? 'Đang tạo...' : 'Đăng ký'}</button>
        <div className="auth-switch">Đã có tài khoản? <Link to="/login">Đăng nhập</Link></div>
      </form>
    </div>
  )
}
