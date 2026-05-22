import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { authApi, setSession } from '../api/client.js'

export default function LoginPage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const data = await authApi.login({ email, password })
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
        <h1>Đăng nhập HTPT Docs</h1>
        <p>Đăng nhập để tạo, chia sẻ và cộng tác trên tài liệu theo thời gian thực.</p>
        <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" placeholder="Email" required />
        <input value={password} onChange={(e) => setPassword(e.target.value)} type="password" placeholder="Mật khẩu" required />
        {error && <div className="error-box">{error}</div>}
        <button className="primary-button" disabled={loading}>{loading ? 'Đang đăng nhập...' : 'Đăng nhập'}</button>
        <div className="auth-switch">Chưa có tài khoản? <Link to="/register">Đăng ký</Link></div>
      </form>
    </div>
  )
}
