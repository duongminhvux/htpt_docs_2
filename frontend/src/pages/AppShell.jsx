import { Outlet, useNavigate } from 'react-router-dom'
import { clearSession, getStoredUser } from '../api/client.js'

export default function AppShell() {
  const navigate = useNavigate()
  const user = getStoredUser()

  function logout() {
    clearSession()
    navigate('/login')
  }

  return (
    <div className="app-shell">
      <div className="global-bar">
        <div className="brand-mark">HTPT Docs</div>
        <div className="global-user">
          <span>{user?.username || user?.email}</span>
          <button className="ghost-button" onClick={logout}>Đăng xuất</button>
        </div>
      </div>
      <Outlet />
    </div>
  )
}
