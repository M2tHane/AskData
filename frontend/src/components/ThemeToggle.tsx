import { Icon } from './Icon'
import { MODE_LABEL, NEXT_MODE, useTheme } from '../state/theme'

/** 侧栏账号区的深浅色切换：浅色 → 深色 → 跟随系统。 */
export function ThemeToggle() {
  const { mode, setMode } = useTheme()
  const next = NEXT_MODE[mode]
  const icon = mode === 'dark' ? 'moon' : mode === 'light' ? 'sun' : 'monitor'
  return (
    <button
      type="button"
      className="cb-icon-btn"
      aria-label={`外观：${MODE_LABEL[mode]}，切换为${MODE_LABEL[next]}`}
      title={`外观：${MODE_LABEL[mode]}（点击切换为${MODE_LABEL[next]}）`}
      onClick={() => setMode(next)}
    >
      <Icon name={icon} />
    </button>
  )
}
