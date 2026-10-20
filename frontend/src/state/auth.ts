import { create } from 'zustand'
import type { CurrentUser, TokenPair } from '../api/types'

const KEY = 'askdata.tokens'

interface AuthState {
  access: string | null
  refresh: string | null
  username: string | null
  user: CurrentUser | null
  setTokens: (pair: TokenPair, username?: string) => void
  setUser: (user: CurrentUser | null) => void
  clear: () => void
}

function load(): { access: string | null; refresh: string | null; username: string | null } {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (raw) return { username: null, ...(JSON.parse(raw) as { access: string | null; refresh: string | null; username?: string | null }) }
  } catch {
    /* corrupted storage is treated as logged out */
  }
  return { access: null, refresh: null, username: null }
}

export const useAuth = create<AuthState>((set) => ({
  ...load(),
  user: null,
  setTokens: (pair, username) => {
    // Rotated tokens keep the name of whoever signed in.
    const next = { access: pair.access_token, refresh: pair.refresh_token, username: username ?? useAuth.getState().username }
    sessionStorage.setItem(KEY, JSON.stringify(next))
    set(next)
  },
  setUser: (user) => set({ user }),
  clear: () => {
    sessionStorage.removeItem(KEY)
    // Unsent chat drafts belong to this user; don't leave them for the next one on a shared browser.
    for (const key of Object.keys(localStorage)) if (key.startsWith('askdata.draft.')) localStorage.removeItem(key)
    set({ access: null, refresh: null, username: null, user: null })
  },
}))
