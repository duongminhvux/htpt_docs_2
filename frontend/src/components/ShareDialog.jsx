import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { docsApi } from '../api/client.js'

export default function ShareDialog({ open, onClose, documentId, role }) {
  const [email, setEmail] = useState('')
  const [shareRole, setShareRole] = useState('editor')
  const [permissions, setPermissions] = useState([])
  const [error, setError] = useState('')

  async function load() {
    if (!open || role !== 'owner') return
    try {
      setPermissions(await docsApi.permissions(documentId))
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => { load() }, [open, documentId, role])

  async function submit(e) {
    e.preventDefault()
    setError('')
    try {
      await docsApi.share(documentId, { email, role: shareRole })
      setEmail('')
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  async function removePermission(permissionId) {
    await docsApi.removePermission(documentId, permissionId)
    await load()
  }

  if (!open) return null

  return (
    <div className="modal-backdrop">
      <div className="share-modal">
        <div className="modal-title-row">
          <h2>Chia sẻ tài liệu</h2>
          <button className="icon-button" onClick={onClose}><X size={18} /></button>
        </div>
        {role !== 'owner' ? (
          <div className="muted">Chỉ owner mới được chia sẻ tài liệu.</div>
        ) : (
          <>
            <form className="share-form" onSubmit={submit}>
              <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" placeholder="Nhập email người dùng đã đăng ký" required />
              <select value={shareRole} onChange={(e) => setShareRole(e.target.value)}>
                <option value="editor">Editor</option>
                <option value="viewer">Viewer</option>
              </select>
              <button className="primary-button">Chia sẻ</button>
            </form>
            {error && <div className="error-box">{error}</div>}
            <div className="permission-list">
              {permissions.map((p) => (
                <div className="permission-row" key={p.id}>
                  <div>
                    <strong>{p.username}</strong>
                    <span>{p.email}</span>
                  </div>
                  <span className="role-pill">{p.role}</span>
                  <button className="ghost-button" onClick={() => removePermission(p.id)}>Xóa</button>
                </div>
              ))}
              {!permissions.length && <div className="muted">Chưa chia sẻ cho ai.</div>}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
