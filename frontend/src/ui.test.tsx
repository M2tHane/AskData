import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { Tabs } from './components/basic'
import { Pager, pageItems } from './components/Pager'
import { Resizer } from './components/Resizer'
import { Select } from './components/Select'
import { ToastProvider, useToast } from './components/Toast'

function Harness({ onChange }: { onChange?: (v: string) => void }) {
  const [value, setValue] = useState<string | null>('a')
  return (
    <Select
      ariaLabel="类型"
      value={value}
      onChange={(v) => {
        setValue(v)
        onChange?.(v)
      }}
      options={[{ value: 'a', label: '选项甲' }, { value: 'b', label: '选项乙' }, { value: 'c', label: '选项丙', disabled: true }]}
    />
  )
}

describe('Select', () => {
  it('is a custom listbox, not a native select', async () => {
    const { container } = render(<Harness />)
    expect(container.querySelector('select')).toBeNull()
    await userEvent.click(screen.getByRole('combobox', { name: '类型' }))
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    expect(screen.getAllByRole('option')).toHaveLength(3)
  })
  it('selects with the mouse and closes', async () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    await userEvent.click(screen.getByRole('combobox'))
    await userEvent.click(screen.getByRole('option', { name: /选项乙/ }))
    expect(onChange).toHaveBeenCalledWith('b')
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(screen.getByRole('combobox')).toHaveTextContent('选项乙')
  })
  it('supports the keyboard and ignores disabled options', async () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    screen.getByRole('combobox').focus()
    await userEvent.keyboard('{ArrowDown}{ArrowDown}{ArrowDown}{Enter}')
    expect(onChange).not.toHaveBeenCalled()
    await userEvent.keyboard('{Escape}{ArrowDown}{ArrowUp}{Enter}')
    expect(screen.queryByRole('listbox')).toBeNull()
  })
})

describe('Toast', () => {
  function Fire() {
    const toast = useToast()
    return <button onClick={() => toast({ tone: 'danger', title: '连接失败', message: '很长很长的原因'.repeat(5) })}>fire</button>
  }
  it('shows a card with title and message, and can be dismissed', async () => {
    render(
      <ToastProvider>
        <Fire />
      </ToastProvider>,
    )
    await userEvent.click(screen.getByText('fire'))
    // Toasts live in one polite status region; each card is a .cb-toast.
    expect(screen.getByRole('status').querySelector('.cb-toast')).toHaveTextContent('连接失败')
    await act(async () => {
      await userEvent.click(screen.getByRole('button', { name: '关闭提示' }))
    })
    expect(screen.getByRole('status').querySelector('.cb-toast')).toBeNull()
  })
})

describe('Resizer', () => {
  it('changes width with the arrow keys within limits and resets on double click', async () => {
    const onChange = vi.fn()
    render(<Resizer label="调整宽度" side="left" value={200} min={160} max={220} defaultValue={180} onChange={onChange} />)
    const bar = screen.getByRole('separator', { name: '调整宽度' })
    bar.focus()
    await userEvent.keyboard('{ArrowRight}')
    expect(onChange).toHaveBeenLastCalledWith(210)
    await userEvent.keyboard('{Shift>}{ArrowRight}{/Shift}')
    expect(onChange).toHaveBeenLastCalledWith(220)
    await userEvent.dblClick(bar)
    expect(onChange).toHaveBeenLastCalledWith(180)
  })
  it('right-side bars grow when dragged left', async () => {
    const onChange = vi.fn()
    render(<Resizer label="右侧" side="right" value={380} min={280} max={720} defaultValue={380} onChange={onChange} />)
    screen.getByRole('separator').focus()
    await userEvent.keyboard('{ArrowLeft}')
    expect(onChange).toHaveBeenLastCalledWith(390)
  })
})

import { MultiSelect } from './components/MultiSelect'
import { ScopeChips } from './components/ScopeChips'

describe('MultiSelect and ScopeChips', () => {
  const pool = [{ id: 1, name: '销售组' }, { id: 2, name: '财务组' }, { id: 3, name: '运营组' }]

  it('toggles several teams without closing and reports the full selection', async () => {
    const onChange = vi.fn()
    render(<MultiSelect ariaLabel="团队" options={pool.map((t) => ({ value: t.id, label: t.name }))} value={[1]} onChange={onChange}>团队</MultiSelect>)
    await userEvent.click(screen.getByRole('button', { name: '团队' }))
    await userEvent.click(screen.getByRole('checkbox', { name: '财务组' }))
    expect(onChange).toHaveBeenLastCalledWith([1, 2])
    await userEvent.click(screen.getByRole('checkbox', { name: '销售组' }))
    expect(onChange).toHaveBeenLastCalledWith([])
    expect(screen.getByRole('listbox')).toBeInTheDocument()
  })

  it('closes on Escape without reaching outer handlers', async () => {
    const outer = vi.fn()
    window.addEventListener('keydown', outer)
    render(<MultiSelect ariaLabel="团队" options={[{ value: 1, label: '甲' }]} value={[]} onChange={() => undefined} />)
    await userEvent.click(screen.getByRole('button', { name: '团队' }))
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(outer).not.toHaveBeenCalled()
    window.removeEventListener('keydown', outer)
  })

  it('shows chips with a remove button and adds from the pool', async () => {
    const onChange = vi.fn()
    render(<ScopeChips label="范围" pool={pool} value={[1, 2]} onChange={onChange} />)
    expect(screen.getByText('销售组')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '移除 财务组' }))
    expect(onChange).toHaveBeenLastCalledWith([1])
    await userEvent.click(screen.getByRole('button', { name: '范围：添加团队' }))
    await userEvent.click(screen.getByRole('checkbox', { name: '运营组' }))
    expect(onChange).toHaveBeenLastCalledWith([1, 2, 3])
  })

  it('lets the caller confirm a removal first', async () => {
    const onRemove = vi.fn()
    const onChange = vi.fn()
    render(<ScopeChips label="范围" pool={pool} value={[1]} onChange={onChange} onRemove={onRemove} />)
    await userEvent.click(screen.getByRole('button', { name: '移除 销售组' }))
    expect(onRemove).toHaveBeenCalledWith(1)
    expect(onChange).not.toHaveBeenCalled()
  })
})

describe('dropdown lists escape transformed dialogs', () => {
  it('renders the Select list under document.body so a dialog transform cannot offset it', async () => {
    const { container } = render(<div style={{ transform: 'translateX(-50%)' }}><Harness /></div>)
    await userEvent.click(screen.getByRole('combobox'))
    const list = screen.getByRole('listbox')
    expect(container.contains(list)).toBe(false)
    expect(list.closest('.cb-sel-pop')?.parentElement).toBe(document.body)
  })
})

describe('Pager', () => {
  it('lists every page up to 7 and folds the middle with gaps beyond that', () => {
    expect(pageItems(1, 3)).toEqual([1, 2, 3])
    expect(pageItems(1, 7)).toEqual([1, 2, 3, 4, 5, 6, 7])
    expect(pageItems(2, 12)).toEqual([1, 2, 3, 4, 5, null, 12])
    expect(pageItems(6, 12)).toEqual([1, null, 5, 6, 7, null, 12])
    expect(pageItems(11, 12)).toEqual([1, null, 8, 9, 10, 11, 12])
  })
  it('stays hidden for a single page and jumps on Enter', async () => {
    const onPage = vi.fn()
    const { container, rerender } = render(<Pager page={1} total={10} onPage={onPage} />)
    expect(container).toBeEmptyDOMElement()
    rerender(<Pager page={1} total={45} onPage={onPage} />)
    expect(screen.getByText('共 45 条 · 每页 10 条')).toBeInTheDocument()
    await userEvent.type(screen.getByRole('textbox', { name: /跳至/ }), '9{Enter}')
    expect(onPage).toHaveBeenLastCalledWith(5)
    await userEvent.click(screen.getByRole('button', { name: '第 3 页' }))
    expect(onPage).toHaveBeenLastCalledWith(3)
  })
})

describe('Tabs', () => {
  it('moves between tabs with the arrow keys', async () => {
    const onChange = vi.fn()
    render(<Tabs items={[{ id: 'a', label: '甲' }, { id: 'b', label: '乙' }]} value="a" onChange={onChange} />)
    screen.getByRole('tab', { name: '甲' }).focus()
    await userEvent.keyboard('{ArrowRight}')
    expect(onChange).toHaveBeenLastCalledWith('b')
    await userEvent.keyboard('{ArrowLeft}')
    expect(onChange).toHaveBeenLastCalledWith('b')
  })
})
