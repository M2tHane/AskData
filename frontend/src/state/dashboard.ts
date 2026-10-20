import { create } from 'zustand'
import type { ChartSpec } from '../api/types'

/**
 * 我的看板（VITE_FEATURE_DASHBOARD）。后端还没有看板接口：固定的卡片先存在本机 localStorage，
 * 刷新时用原回答的 replay 接口重新执行原 SQL。后端接好后换成服务端存储。
 */
export interface Pin {
  id: string
  question: string
  conversationId: number
  messageId: number
  datasource: string
  chart?: ChartSpec | null
  pinnedAt: string
}

const KEY = 'askdata.dashboard'

function load(): Pin[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '[]') as Pin[]
  } catch {
    return []
  }
}

function save(pins: Pin[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(pins))
  } catch {
    /* not persisted */
  }
}

interface DashboardState {
  pins: Pin[]
  pin: (p: Omit<Pin, 'id' | 'pinnedAt'>) => void
  unpin: (id: string) => Pin | undefined
  restore: (p: Pin) => void
}

export const useDashboard = create<DashboardState>((set, get) => ({
  pins: load(),
  pin: (p) => {
    if (get().pins.some((x) => x.messageId === p.messageId)) return
    const pins = [...get().pins, { ...p, id: `${p.messageId}-${Date.now()}`, pinnedAt: new Date().toISOString() }]
    save(pins)
    set({ pins })
  },
  unpin: (id) => {
    const gone = get().pins.find((x) => x.id === id)
    const pins = get().pins.filter((x) => x.id !== id)
    save(pins)
    set({ pins })
    return gone
  },
  restore: (p) => {
    const pins = [...get().pins, p].sort((a, b) => a.pinnedAt.localeCompare(b.pinnedAt))
    save(pins)
    set({ pins })
  },
}))
