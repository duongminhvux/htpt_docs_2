import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import ReactQuill, { Quill } from 'react-quill'
import QuillCursors from 'quill-cursors'
import { ArrowLeft, Clock3, Lock, Redo2, Share2, Undo2 } from 'lucide-react'
import { docsApi, getStoredUser, getToken, WS_URL } from '../api/client.js'
import ShareDialog from '../components/ShareDialog.jsx'
import PresenceBar from '../components/PresenceBar.jsx'
import HistoryPanel from '../components/HistoryPanel.jsx'

if (!Quill.imports['modules/cursors']) {
  Quill.register('modules/cursors', QuillCursors)
}

const EMPTY_DELTA = { ops: [{ insert: '\n' }] }

function summarizeDelta(delta) {
  if (!delta) return 'empty'

  let index = 0
  const parts = []

  for (const op of delta.ops || []) {
    if (op.retain) {
      index += Number(op.retain)
      continue
    }

    if (op.insert !== undefined) {
      const value = typeof op.insert === 'string'
        ? op.insert.replace(/\n/g, '\\n')
        : String(op.insert)

      parts.push(`insert='${value}' at index=${index}`)
      index += typeof op.insert === 'string' ? op.insert.length : 1
      continue
    }

    if (op.delete) {
      parts.push(`delete=${op.delete} at index=${index}`)
    }
  }

  return parts.join('; ') || 'format/retain-only'
}

function createClientOpId(userId) {
  return `${userId || 'client'}-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function getOfflineQueueKey(documentId, userId) {
  return `offline-ops:${documentId}:${userId || 'anonymous'}`
}

function readOfflineQueue(key) {
  try {
    const value = localStorage.getItem(key)
    const parsed = value ? JSON.parse(value) : []
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function writeOfflineQueue(key, ops) {
  if (!ops.length) {
    localStorage.removeItem(key)
    return
  }

  localStorage.setItem(key, JSON.stringify(ops))
}

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
  const vectorClockRef = useRef({})
  const lastServerDeltaRef = useRef(EMPTY_DELTA)
  const lastServerVersionRef = useRef(0)
  const pendingOpsRef = useRef([])
  const offlineOpsRef = useRef([])
  const reconnectTimerRef = useRef(null)
  const unmountedRef = useRef(false)
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
  const [busyAction, setBusyAction] = useState('')
  const [offlineCount, setOfflineCount] = useState(0)

  const canEdit = role === 'owner' || role === 'editor'
  const offlineQueueKey = useMemo(
    () => getOfflineQueueKey(documentId, user?.id),
    [documentId, user?.id]
  )

  const persistOfflineQueue = useCallback((ops) => {
    offlineOpsRef.current = ops
    pendingOpsRef.current = ops
    setOfflineCount(ops.length)
    writeOfflineQueue(offlineQueueKey, ops)
  }, [offlineQueueKey])

  const sendQueuedOperation = useCallback((op) => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return false

    wsRef.current.send(JSON.stringify({
      type: 'operation',
      operation_delta: op.delta,
      base_version: op.baseVersion,
      vector_clock: op.vectorClock,
      client_id: user?.id,
      client_op_id: op.clientOpId
    }))

    return true
  }, [user?.id])

  const flushOfflineQueue = useCallback(() => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return

    const ops = offlineOpsRef.current
    if (!ops.length) return

    console.info('[CLIENT_FLUSH_OFFLINE_QUEUE]', {
      doc: documentId,
      count: ops.length,
      ids: ops.map((item) => item.clientOpId)
    })

    for (const op of ops) {
      sendQueuedOperation(op)
    }

    persistOfflineQueue(ops.map((op) => ({ ...op, sent: true })))
  }, [documentId, persistOfflineQueue, sendQueuedOperation])

  const modules = useMemo(() => ({
    cursors: {
      hideDelay: 10000,
      hideSpeedMs: 300,
      transformOnTextChange: true
    },
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

    // Tắt history local của Quill.
    // App này dùng collaborative undo/redo qua server.
    history: {
      delay: 500,
      maxStack: 0,
      userOnly: true
    }
  }), [])

  const applyServerDocument = useCallback((contentDelta, serverVersion, serverClock, options = {}) => {
    const quill = quillRef.current?.getEditor()
    if (!quill || !contentDelta) return

    const { force = false, action = 'sync' } = options
    const normalizedVersion = Number(serverVersion || 0)

    // Không apply snapshot cũ, tránh client bị quay ngược về version cũ.
    if (!force && normalizedVersion < lastServerVersionRef.current) {
      console.info('[CLIENT_SKIP_STALE_SNAPSHOT]', {
        doc: documentId,
        incomingVersion: normalizedVersion,
        currentServerVersion: lastServerVersionRef.current,
        action
      })
      return
    }

    lastServerDeltaRef.current = contentDelta || EMPTY_DELTA
    lastServerVersionRef.current = normalizedVersion

    const selection = quill.getSelection()
    readyRef.current = false

    // Server là nguồn sự thật.
    // Sau đó re-apply các local operation chưa được ACK để người đang gõ không mất chữ.
    quill.setContents(lastServerDeltaRef.current, 'api')

    for (const pending of pendingOpsRef.current) {
      quill.updateContents(pending.delta, 'api')
    }

    quill.enable(canEdit)

    if (selection) {
      const maxIndex = Math.max(0, quill.getLength() - 1)
      const nextIndex = Math.min(selection.index, maxIndex)
      const nextLength = Math.min(selection.length || 0, Math.max(0, maxIndex - nextIndex))

      quill.setSelection(nextIndex, nextLength, 'silent')
    }

    setVersion(normalizedVersion)
    versionRef.current = normalizedVersion

    setVectorClock(serverClock || {})
    vectorClockRef.current = serverClock || {}

    console.info('[CLIENT_APPLY_SERVER_SNAPSHOT]', {
      doc: documentId,
      serverVersion: normalizedVersion,
      pendingCount: pendingOpsRef.current.length,
      action,
      text: quill.getText()
    })

    setTimeout(() => {
      readyRef.current = true
    }, 30)
  }, [canEdit, documentId])

  useEffect(() => {
    const saved = readOfflineQueue(offlineQueueKey)
    offlineOpsRef.current = saved
    pendingOpsRef.current = saved
    setOfflineCount(saved.length)
  }, [offlineQueueKey])

  useEffect(() => {
    let mounted = true

    docsApi.get(documentId)
      .then((doc) => {
        if (!mounted) return

        setTitle(doc.title)
        setRole(doc.role)
        setVersion(doc.version)

        versionRef.current = doc.version
        lastServerVersionRef.current = doc.version
        lastServerDeltaRef.current = doc.content_delta || EMPTY_DELTA

        setVectorClock(doc.vector_clock || {})
        vectorClockRef.current = doc.vector_clock || {}
      })
      .catch((err) => {
        if (!mounted) return
        setError(err.message)
      })

    return () => {
      mounted = false
    }
  }, [documentId])

  useEffect(() => {
    unmountedRef.current = false
    const token = getToken()
    if (!token) return

    function clearReconnectTimer() {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
    }

    function scheduleReconnect() {
      if (unmountedRef.current) return
      clearReconnectTimer()
      reconnectTimerRef.current = setTimeout(() => {
        connectSocket()
      }, 1200)
    }

    function connectSocket() {
      if (unmountedRef.current) return

      const ws = new WebSocket(`${WS_URL}/documents/${documentId}?token=${encodeURIComponent(token)}`)
      wsRef.current = ws

      const isCurrentSocket = () => wsRef.current === ws

      ws.onopen = () => {
        if (!isCurrentSocket()) return

        setConnected(true)
        setError((prev) => (
          prev === 'Không kết nối được WebSocket' ? '' : prev
        ))

        flushOfflineQueue()
      }

      ws.onclose = () => {
        if (!isCurrentSocket()) return

        setConnected(false)
        scheduleReconnect()
      }

      ws.onerror = () => {
        if (!isCurrentSocket()) return

        setConnected(false)
        setError('Không kết nối được WebSocket')
      }

      ws.onmessage = (event) => {
        if (!isCurrentSocket()) return

        let data

        try {
          data = JSON.parse(event.data)
        } catch (err) {
          console.error('[CLIENT_WS_PARSE_ERROR]', err, event.data)
          return
        }

        const quill = quillRef.current?.getEditor()

        if (data.type === 'init') {
          setRole(data.role)
          setUsers(data.users || [])

          // Giữ lại các operation offline/pending để re-apply lên snapshot server.
          pendingOpsRef.current = offlineOpsRef.current

          applyServerDocument(
            data.content_delta || EMPTY_DELTA,
            data.version || 0,
            data.vector_clock || {},
            { force: true, action: 'init' }
          )

          if (quill) {
            quill.enable(data.role === 'owner' || data.role === 'editor')
          }

          flushOfflineQueue()
          return
        }

        if (data.type === 'presence') {
          setUsers(data.users || [])
          return
        }

        if (data.type === 'role_changed') {
          setRole(data.role)

          const canNowEdit = data.role === 'owner' || data.role === 'editor'
          quill?.enable(canNowEdit)

          setError(`Quyền của bạn đã đổi thành ${data.role}`)
          return
        }

        if (data.type === 'access_removed' || data.type === 'document_deleted') {
          alert(data.message || 'Bạn không còn quyền truy cập tài liệu này')
          navigate('/')
          return
        }

        if (data.type === 'cursor' && data.user_id !== user?.id && quill) {
          const cursors = quill.getModule('cursors')
          const name = data.username || 'User'
          const color = data.color || '#1a73e8'
          const cursorId = data.user_id

          if (!data.cursor) {
            cursors.removeCursor(cursorId)
          } else {
            cursors.createCursor(cursorId, name, color)
            cursors.moveCursor(cursorId, data.cursor)
          }

          return
        }

        if (data.type === 'operation_applied') {
          console.info('[CLIENT_RECV_APPLIED]', {
            doc: documentId,
            fromUser: data.user_id,
            clientOpId: data.client_op_id,
            serverVersion: data.server_version,
            delta: summarizeDelta(data.operation_delta),
            action: data.action,
            text: data.content_text
          })

          const wasPendingLocalOp = data.client_op_id
            ? offlineOpsRef.current.some((item) => item.clientOpId === data.client_op_id)
            : false

          if (data.client_op_id) {
            const nextQueue = offlineOpsRef.current.filter(
              (item) => item.clientOpId !== data.client_op_id
            )
            persistOfflineQueue(nextQueue)
          }

          const action = data.action || 'edit'
          const normalizedVersion = Number(data.server_version || 0)

          lastServerDeltaRef.current = data.content_delta || EMPTY_DELTA
          lastServerVersionRef.current = normalizedVersion

          setVersion(normalizedVersion)
          setVectorClock(data.vector_clock || {})
          vectorClockRef.current = data.vector_clock || {}

          const isAckOfMyTyping =
            data.user_id === user?.id &&
            wasPendingLocalOp &&
            action === 'edit'

          if (isAckOfMyTyping) {
            if (offlineOpsRef.current.length === 0) {
              versionRef.current = normalizedVersion
            }

            return
          }

          applyServerDocument(
            data.content_delta || EMPTY_DELTA,
            data.server_version || 0,
            data.vector_clock || {},
            { action: action || 'operation_applied' }
          )

          return
        }

        if (data.type === 'error') {
          setError(data.message)
        }
      }
    }

    connectSocket()

    function onBrowserOnline() {
      scheduleReconnect()
    }

    window.addEventListener('online', onBrowserOnline)

    return () => {
      unmountedRef.current = true
      clearReconnectTimer()
      window.removeEventListener('online', onBrowserOnline)

      const ws = wsRef.current
      wsRef.current = null

      try {
        ws?.close(1000, 'component cleanup')
      } catch {
        // ignore
      }
    }
  }, [documentId, user?.id, navigate, applyServerDocument, flushOfflineQueue, persistOfflineQueue])

  const onChange = useCallback((content, delta, source) => {
    if (source !== 'user' || !readyRef.current || !canEdit) return

    const clientOpId = createClientOpId(user?.id)
    const baseVersion = versionRef.current
    const nextClock = tickClock(vectorClockRef.current, user?.id || 'client')

    setVectorClock(nextClock)
    vectorClockRef.current = nextClock

    console.info('[CLIENT_SEND_OP]', {
      client: user?.id,
      username: user?.username,
      doc: documentId,
      clientOpId,
      baseVersion,
      delta: summarizeDelta(delta)
    })

    const op = {
      clientOpId,
      delta,
      baseVersion,
      vectorClock: nextClock,
      sent: false,
      createdAt: Date.now()
    }

    const nextQueue = [...offlineOpsRef.current, op]
    persistOfflineQueue(nextQueue)

    if (sendQueuedOperation(op)) {
      persistOfflineQueue(nextQueue.map((item) => (
        item.clientOpId === clientOpId ? { ...item, sent: true } : item
      )))
    } else {
      setConnected(false)
      setError('Đang offline: thay đổi đã lưu local, khi có mạng sẽ tự đồng bộ')
    }
  }, [canEdit, documentId, persistOfflineQueue, sendQueuedOperation, user?.id, user?.username])

  const onSelectionChange = useCallback((range, source) => {
    if (source !== 'user') return

    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'cursor',
        cursor: range ? { index: range.index, length: range.length || 0 } : null
      }))
    }
  }, [])

  async function saveTitle() {
    if (!canEdit) return

    try {
      await docsApi.update(documentId, { title })
    } catch (err) {
      setError(err.message)
    }
  }

  async function collaborativeUndo() {
    if (!canEdit || busyAction) return

    setBusyAction('undo')
    setError('')

    try {
      const doc = await docsApi.undo(documentId)

      applyServerDocument(
        doc.content_delta || EMPTY_DELTA,
        doc.version || 0,
        doc.vector_clock || {},
        { force: true, action: 'undo_rest_response' }
      )
    } catch (err) {
      setError(err.message)
    } finally {
      setBusyAction('')
    }
  }

  async function collaborativeRedo() {
    if (!canEdit || busyAction) return

    setBusyAction('redo')
    setError('')

    try {
      const doc = await docsApi.redo(documentId)

      applyServerDocument(
        doc.content_delta || EMPTY_DELTA,
        doc.version || 0,
        doc.vector_clock || {},
        { force: true, action: 'redo_rest_response' }
      )
    } catch (err) {
      setError(err.message)
    } finally {
      setBusyAction('')
    }
  }

  useEffect(() => {
    const quill = quillRef.current?.getEditor()
    if (!quill) return

    const root = quill.root

    function onKeyDown(event) {
      const key = event.key.toLowerCase()

      if ((event.ctrlKey || event.metaKey) && (key === 'z' || key === 'y')) {
        event.preventDefault()

        if (key === 'z') collaborativeUndo()
        if (key === 'y') collaborativeRedo()
      }
    }

    root.addEventListener('keydown', onKeyDown)

    return () => {
      root.removeEventListener('keydown', onKeyDown)
    }
  })

  return (
    <div className="editor-layout">
      <header className="docs-topbar">
        <button className="icon-button" onClick={() => navigate('/')}>
          <ArrowLeft size={20} />
        </button>

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
            <span>File</span>
            <span>Edit</span>
            <span>View</span>
            <span>Insert</span>
            <span>Format</span>
            <span>Tools</span>
            <span>Extensions</span>
            <span>Help</span>
          </div>
        </div>

        <div className="topbar-spacer" />

        <button
          className="ghost-button"
          disabled={!canEdit || busyAction === 'undo'}
          onClick={collaborativeUndo}
        >
          <Undo2 size={16} /> Undo
        </button>

        <button
          className="ghost-button"
          disabled={!canEdit || busyAction === 'redo'}
          onClick={collaborativeRedo}
        >
          <Redo2 size={16} /> Redo
        </button>

        <div className={`connection-pill ${connected ? 'online' : 'offline'}`}>
          {connected
            ? (offlineCount ? `Syncing ${offlineCount}` : 'Realtime')
            : (offlineCount ? `Offline · ${offlineCount} pending` : 'Offline')}
        </div>

        <PresenceBar users={users} />

        {role === 'viewer' && (
          <div className="viewer-pill">
            <Lock size={14} /> Viewer
          </div>
        )}

        <button className="ghost-button" onClick={() => setHistoryOpen((v) => !v)}>
          <Clock3 size={16} /> History
        </button>

        <button className="share-button" onClick={() => setShareOpen(true)}>
          <Share2 size={16} /> Share
        </button>
      </header>

      <div className="editor-body">
        <aside className="doc-tabs-sidebar">
          <button className="back-arrow" onClick={() => navigate('/')}>‹</button>

          <div className="tabs-title-row">
            <span>Document tabs</span>
            <button>+</button>
          </div>

          <div className="tab-item active">
            <span>▣</span> Tab 1 <span className="dot-menu">⋮</span>
          </div>

          <p>Headings you add to the document will appear here.</p>
        </aside>

        <main className="paper-zone">
          {error && <div className="floating-error">{error}</div>}

          <div className="ruler-horizontal">
            <span>1</span>
            <span>2</span>
            <span>3</span>
            <span>4</span>
            <span>5</span>
            <span>6</span>
            <span>7</span>
          </div>

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

          <div className="version-chip">
            v{version} · {role}
          </div>
        </main>

        <HistoryPanel open={historyOpen} documentId={documentId} canEdit={canEdit} />
      </div>

      <ShareDialog
        open={shareOpen}
        onClose={() => setShareOpen(false)}
        documentId={documentId}
        role={role}
      />
    </div>
  )
}