export const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api'
export const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws'

export function getToken() {
  return localStorage.getItem('token')
}

export function setSession(token, user) {
  localStorage.setItem('token', token)
  localStorage.setItem('user', JSON.stringify(user))
}

export function clearSession() {
  localStorage.removeItem('token')
  localStorage.removeItem('user')
}

export function getStoredUser() {
  try {
    return JSON.parse(localStorage.getItem('user') || 'null')
  } catch {
    return null
  }
}

export async function apiFetch(path, options = {}) {
  const token = getToken()
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {})
  }
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers
  })

  if (response.status === 204) return null

  let data = null
  try {
    data = await response.json()
  } catch {
    data = null
  }

  if (!response.ok) {
    const message = data?.detail || data?.message || `HTTP ${response.status}`
    throw new Error(message)
  }
  return data
}

export const authApi = {
  register: (payload) => apiFetch('/auth/register', { method: 'POST', body: JSON.stringify(payload) }),
  login: (payload) => apiFetch('/auth/login', { method: 'POST', body: JSON.stringify(payload) }),
  me: () => apiFetch('/auth/me')
}

export const docsApi = {
  list: () => apiFetch('/documents'),
  create: (title = 'Untitled document') => apiFetch('/documents', { method: 'POST', body: JSON.stringify({ title }) }),
  get: (id) => apiFetch(`/documents/${id}`),
  update: (id, payload) => apiFetch(`/documents/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  remove: (id) => apiFetch(`/documents/${id}`, { method: 'DELETE' }),
  permissions: (id) => apiFetch(`/documents/${id}/permissions`),
  share: (id, payload) => apiFetch(`/documents/${id}/share`, { method: 'POST', body: JSON.stringify(payload) }),
  removePermission: (id, permissionId) => apiFetch(`/documents/${id}/permissions/${permissionId}`, { method: 'DELETE' }),
  history: (id) => apiFetch(`/documents/${id}/history`)
}
