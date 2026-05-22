import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import ReactQuill from 'react-quill'
import { ArrowLeft, Clock3, Lock, Share2, Star } from 'lucide-react'
import { docsApi, getStoredUser, getToken, WS_URL } from '../api/client.js'
import ShareDialog from '../components/ShareDialog.jsx'
import PresenceBar from '../components/PresenceBar.jsx'
import HistoryPanel from '../components/HistoryPanel.jsx'

const EMPTY_DELTA = { ops: [{ insert: '\n' }] }

function tickClock(clock, nodeId) {
  const next = { ...(clock || {}) }
  next[nodeId] = (next[nodeId] || 0) + 1
  return next
}

export default function EditorPage() {
  const { documentId } = useParams()
  const navigate = useNavigate()
  const quillRef = useRef(null)
  const wsRef = useRef(null)
  const readyRef = useRef(false)
  const versionRef = useRef(0)
  const user = getStoredUser()

  const [title, setTitle] = useState('Untitled document')
  const [role, setRole] = useState('viewer')
  const [version, setVersion] = useState(0)
  const [vectorClock, setVectorClock] = useState({})
  const [users, setUsers] = useState([])
  const [connected, setConnected] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [error, setError] = useState('')

  const canEdit = role === 'owner' || role === 'editor'

  const modules = useMemo(() => ({
    toolbar: [
      [{ header: [1, 2, 3, false] }],
      [{ font: [] }, { size: ['small', false, 'large', 'huge'] }],
      ['bold', 'italic', 'underline', 'strike'],
      [{ color: [] }, { background: [] }],
      [{ align: [] }],
      [{ list: 'ordered' }, { list: 'bullet' }],
      [{ indent: '-1' }, { indent: '+1' }],
      ['blockquote', 'code-block'],
      ['link', 'image'],
      ['clean']
    ],
    history: {
      delay: 500,
      maxStack: 100,
      userOnly: true
    }
  }), [])

  useEffect(() => {
    let mounted = true
    docsApi.get(documentId)
      .then((doc) => {
        if (!mounted) return
        setTitle(doc.title)
        setRole(doc.role)
        setVersion(doc.version)
        versionRef.current = doc.version
        setVectorClock(doc.vector_clock || {})
      })
      .catch((err) => setError(err.message))
    return () => { mounted = false }
  }, [documentId])

  useEffect(() => {
    const token = getToken()
    if (!token) return
    const ws = new WebSocket(`${WS_URL}/documents/${documentId}?token=${encodeURIComponent(token)}`)
    wsRef.current = ws

    ws.onopen = () => setConnected(true)
    ws.onclose = () => setConnected(false)
    ws.onerror = () => setError('Không kết nối được WebSocket')
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      const quill = quillRef.current?.getEditor()

      if (data.type === 'init') {
        setRole(data.role)
        setUsers(data.users || [])
        setVersion(data.version || 0)
        versionRef.current = data.version || 0
        setVectorClock(data.vector_clock || {})
        if (quill) {
          readyRef.current = false
          quill.setContents(data.content_delta || EMPTY_DELTA, 'api')
          quill.enable(data.role === 'owner' || data.role === 'editor')
          setTimeout(() => { readyRef.current = true }, 50)
        }
      }

      if (data.type === 'presence') setUsers(data.users || [])

      if (data.type === 'operation_applied') {
        setVersion(data.server_version)
        versionRef.current = data.server_version
        setVectorClock(data.vector_clock || {})
        if (quill && data.user_id !== user?.id) {
          const selection = quill.getSelection()
          quill.updateContents(data.operation_delta, 'api')
          if (selection) quill.setSelection(selection, 'silent')
        }
      }

      if (data.type === 'error') setError(data.message)
    }

    return () => ws.close()
  }, [documentId, user?.id])

  const onChange = useCallback((content, delta, source) => {
    if (source !== 'user' || !readyRef.current || !canEdit) return
    const nextClock = tickClock(vectorClock, user?.id || 'client')
    setVectorClock(nextClock)
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'operation',
        operation_delta: delta,
        base_version: versionRef.current,
        vector_clock: nextClock,
        client_id: user?.id
      }))
    }
  }, [canEdit, user?.id, vectorClock])

  function onSelectionChange(range, source) {
    if (source !== 'user') return
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'cursor', cursor: range?.index ?? null }))
    }
  }

  async function saveTitle() {
    if (!canEdit) return
    try {
      await docsApi.update(documentId, { title })
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="editor-layout">
      <header className="docs-topbar">
        <button className="icon-button" onClick={() => navigate('/')}><ArrowLeft size={20} /></button>
        <div className="doc-file-icon">▣</div>
        <div className="doc-title-stack">
          <input
            className="doc-title-input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={saveTitle}
            readOnly={!canEdit}
          />
          <div className="docs-menu-row">
            <span>File</span><span>Edit</span><span>View</span><span>Insert</span><span>Format</span><span>Tools</span><span>Extensions</span><span>Help</span>
          </div>
        </div>
        <div className="topbar-spacer" />
        <div className={`connection-pill ${connected ? 'online' : 'offline'}`}>{connected ? 'Realtime' : 'Offline'}</div>
        <PresenceBar users={users} />
        {role === 'viewer' && <div className="viewer-pill"><Lock size={14} /> Viewer</div>}
        <button className="ghost-button" onClick={() => setHistoryOpen((v) => !v)}><Clock3 size={16} /> History</button>
        <button className="share-button" onClick={() => setShareOpen(true)}><Share2 size={16} /> Share</button>
      </header>

      <div className="editor-body">
        <aside className="doc-tabs-sidebar">
          <button className="back-arrow" onClick={() => navigate('/')}>‹</button>
          <div className="tabs-title-row"><span>Document tabs</span><button>+</button></div>
          <div className="tab-item active"><span>▣</span> Tab 1 <span className="dot-menu">⋮</span></div>
          <p>Headings you add to the document will appear here.</p>
        </aside>

        <main className="paper-zone">
          {error && <div className="floating-error">{error}</div>}
          <div className="ruler-horizontal"><span>1</span><span>2</span><span>3</span><span>4</span><span>5</span><span>6</span><span>7</span></div>
          <ReactQuill
            ref={quillRef}
            theme="snow"
            readOnly={!canEdit}
            modules={modules}
            defaultValue={EMPTY_DELTA}
            onChange={onChange}
            onChangeSelection={onSelectionChange}
            placeholder="Write a document about..."
          />
          <div className="ai-prompt-pill">
            <button>+</button>
            <span>Write a document about...</span>
            <button>↑</button>
          </div>
          <div className="version-chip">v{version} · {role}</div>
        </main>

        <HistoryPanel open={historyOpen} documentId={documentId} />
      </div>
      <ShareDialog open={shareOpen} onClose={() => setShareOpen(false)} documentId={documentId} role={role} />
    </div>
  )
}
