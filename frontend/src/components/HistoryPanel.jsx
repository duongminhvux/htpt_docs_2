import { useEffect, useState } from 'react'
import { docsApi } from '../api/client.js'

export default function HistoryPanel({ open, documentId, canEdit }) {
  const [versions, setVersions] = useState([])
  const [ops, setOps] = useState([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function load() {
    if (!open) return
    setError('')
    try {
      const [versionRows, operationRows] = await Promise.all([
        docsApi.versions(documentId),
        docsApi.history(documentId)
      ])
      setVersions(versionRows)
      setOps(operationRows)
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => { load() }, [open, documentId])

  async function rollback(version) {
    if (!canEdit) return
    const ok = window.confirm(`Rollback tài liệu về version ${version}? Thao tác này sẽ tạo một version mới cho tất cả người đang mở.`)
    if (!ok) return
    setBusy(true)
    setError('')
    try {
      await docsApi.rollback(documentId, version)
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (!open) return null

  return (
    <aside className="history-panel">
      <h3>Lịch sử & versioning</h3>
      {error && <div className="error-box">{error}</div>}

      <div className="history-section-title">Version snapshots</div>
      {versions.map((item) => (
        <div className="history-item" key={item.id}>
          <div><strong>v{item.version}</strong> · {item.username || 'System'}</div>
          <span>{item.action}{item.target_version !== null && item.target_version !== undefined ? ` → v${item.target_version}` : ''}</span>
          <small>{new Date(item.created_at).toLocaleString()}</small>
          {item.content_text && <small className="history-preview">{item.content_text}</small>}
          {canEdit && (
            <button className="ghost-button small" disabled={busy} onClick={() => rollback(item.version)}>
              Rollback về v{item.version}
            </button>
          )}
        </div>
      ))}
      {!versions.length && <div className="muted">Chưa có version nào.</div>}

      <div className="history-section-title">Operation log</div>
      {ops.slice(0, 20).map((item) => (
        <div className="history-item compact" key={item.id}>
          <div><strong>op v{item.server_version}</strong> · {item.username || 'Unknown'}</div>
          <span>{item.causal_relation} · base v{item.base_version}</span>
          <small>{new Date(item.created_at).toLocaleString()}</small>
        </div>
      ))}
      {!ops.length && <div className="muted">Chưa có operation nào.</div>}
    </aside>
  )
}
