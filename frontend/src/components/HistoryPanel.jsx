import { useEffect, useState } from 'react'
import { docsApi } from '../api/client.js'

export default function HistoryPanel({ open, documentId }) {
  const [items, setItems] = useState([])
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    docsApi.history(documentId).then(setItems).catch((err) => setError(err.message))
  }, [open, documentId])

  if (!open) return null

  return (
    <aside className="history-panel">
      <h3>Lịch sử chỉnh sửa</h3>
      {error && <div className="error-box">{error}</div>}
      {items.map((item) => (
        <div className="history-item" key={item.id}>
          <div><strong>v{item.server_version}</strong> · {item.username || 'Unknown'}</div>
          <span>{item.causal_relation} · base v{item.base_version}</span>
          <small>{new Date(item.created_at).toLocaleString()}</small>
        </div>
      ))}
      {!items.length && <div className="muted">Chưa có operation nào.</div>}
    </aside>
  )
}
