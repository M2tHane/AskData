import { create } from 'zustand'

export type ThemeMode = 'system' | 'light' | 'dark'

const KEY = 'askdata.theme'
const media = () => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null)

function read(): ThemeMode {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'light' || v === 'dark' || v === 'system') return v
  } catch {
    /* storage blocked: follow the system */
  }
  return 'system'
}

function resolve(mode: ThemeMode): 'light' | 'dark' {
  if (mode !== 'system') return mode
  return media()?.matches ? 'dark' : 'light'
}

/** 「跟随系统」时不写 data-theme，交给 tokens.css 里的 prefers-color-scheme。 */
function apply(mode: ThemeMode) {
  const root = document.documentElement
  if (mode === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', mode)
}

interface ThemeState {
  mode: ThemeMode
  /** 实际生效的深浅色，跟随系统时随系统变化。 */
  resolved: 'light' | 'dark'
  setMode: (mode: ThemeMode) => void
}

export const useTheme = create<ThemeState>((set) => ({
  mode: read(),
  resolved: resolve(read()),
  setMode: (mode) => {
    try {
      localStorage.setItem(KEY, mode)
    } catch {
      /* not persisted; still applies for this tab */
    }
    apply(mode)
    set({ mode, resolved: resolve(mode) })
  },
}))

/** 启动时调用一次：应用保存的设置，并在跟随系统时监听系统切换。 */
export function initTheme() {
  apply(useTheme.getState().mode)
  media()?.addEventListener?.('change', () => {
    const { mode } = useTheme.getState()
    if (mode === 'system') useTheme.setState({ resolved: resolve(mode) })
  })
}

/** 浅色 → 深色 → 跟随系统，循环切换。 */
export const NEXT_MODE: Record<ThemeMode, ThemeMode> = { light: 'dark', dark: 'system', system: 'light' }
export const MODE_LABEL: Record<ThemeMode, string> = { light: '浅色', dark: '深色', system: '跟随系统' }
