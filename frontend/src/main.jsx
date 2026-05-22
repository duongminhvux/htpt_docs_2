import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import 'react-quill/dist/quill.snow.css'
import './styles.css'
import AppShell from './pages/AppShell.jsx'
import LoginPage from './pages/LoginPage.jsx'
import RegisterPage from './pages/RegisterPage.jsx'
import DocumentsPage from './pages/DocumentsPage.jsx'
import EditorPage from './pages/EditorPage.jsx'

function ProtectedRoute({ children }) {
  const token = localStorage.getItem('token')
  if (!token) return <Navigate to="/login" replace />
  return children
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <BrowserRouter>
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <AppShell />
          </ProtectedRoute>
        }
      >
        <Route index element={<DocumentsPage />} />
        <Route path="documents/:documentId" element={<EditorPage />} />
      </Route>
    </Routes>
  </BrowserRouter>
)
