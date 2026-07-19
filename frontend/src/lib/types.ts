// Mirrors backend/app/schemas/. Kept hand-written rather than generated: the
// surface is small, and a generator would be one more thing to run in CI.

export interface User {
  id: string
  email: string
  full_name: string | null
  is_demo: boolean
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
}

export type DocumentStatus = 'pending' | 'processing' | 'ready' | 'failed'

export interface Document {
  id: string
  filename: string
  title: string | null
  status: DocumentStatus
  error: string | null
  page_count: number
  chunk_count: number
  size_bytes: number
  created_at: string
  is_shared: boolean
}

export interface Citation {
  index: number
  document_id: string
  filename: string
  page_no: number
  section_path: string
  excerpt: string
}

export interface ChatResponse {
  answer: string
  citations: Citation[]
  grounded: boolean
}
