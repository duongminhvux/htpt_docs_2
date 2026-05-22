import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileText, Plus, Trash2 } from 'lucide-react'
import { docsApi } from '../api/client.js'

export default function DocumentsPage() {
  const navigate = useNavigate()
  const [documents, setDocuments] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  async function load() {
    setLoading(true)
    setError('')
    try {
      setDocuments(await docsApi.list())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function createDoc() {
    const doc = await docsApi.create('Untitled document')
    navigate(`/documents/${doc.id}`)
  }

  async function removeDoc(e, doc) {
    e.stopPropagation()
    if (!confirm(`Xóa tài liệu "${doc.title}"?`)) return
    await docsApi.remove(doc.id)
    await load()
  }

  return (
    <main className="docs-home">
      <section className="template-strip">
        <div className="template-header">
          <h2>Bắt đầu tài liệu mới</h2>
        </div>
        <button className="blank-template" onClick={createDoc}>
          <div className="blank-plus"><Plus size={38} /></div>
          <span>Trống</span>
        </button>
      </section>

      <section className="docs-list-section">
        <div className="section-title-row">
          <h2>Tài liệu gần đây</h2>
          <button className="primary-button small" onClick={createDoc}><Plus size={16} /> Tạo mới</button>
        </div>
        {error && <div className="error-box">{error}</div>}
        {loading ? <div className="muted">Đang tải...</div> : (
          <div className="docs-grid">
            {documents.map((doc) => (
              <div className="doc-card" key={doc.id} onClick={() => navigate(`/documents/${doc.id}`)}>
                <div className="doc-preview"><FileText size={52} /></div>
                <div className="doc-meta">
                  <strong>{doc.title}</strong>
                  <span>{doc.role} · v{doc.version}</span>
                </div>
                {doc.role === 'owner' && (
                  <button className="icon-button danger" title="Xóa" onClick={(e) => removeDoc(e, doc)}><Trash2 size={16} /></button>
                )}
              </div>
            ))}
            {!documents.length && <div className="empty-state">Chưa có tài liệu nào. Bấm “Tạo mới” để bắt đầu.</div>}
          </div>
        )}
      </section>
    </main>
  )
}
