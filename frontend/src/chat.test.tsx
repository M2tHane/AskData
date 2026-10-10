import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ClarifyPrompt, Composer, ResultTable, SqlBlock } from './components/chat'

describe('Composer', () => {
  it('submits on Enter and clears, ignores empty input', async () => {
    const onSubmit = vi.fn()
    render(<Composer source="pg · sales" onSubmit={onSubmit} />)
    const box = screen.getByLabelText('提问')
    await userEvent.type(box, '{Enter}')
    expect(onSubmit).not.toHaveBeenCalled()
    await userEvent.type(box, '上个月回款{Enter}')
    expect(onSubmit).toHaveBeenCalledWith('上个月回款', [])
    expect(box).toHaveValue('')
  })
  it('shows stop while streaming', () => {
    render(<Composer source="pg · sales" streaming onSubmit={() => undefined} />)
    expect(screen.getByRole('button', { name: '停止' })).toBeInTheDocument()
  })
})

describe('ClarifyPrompt', () => {
  it('reports the chosen label', async () => {
    const onPick = vi.fn()
    render(<ClarifyPrompt question="哪种口径？" options={['下单金额', '回款金额']} onPick={onPick} />)
    await userEvent.click(screen.getByRole('button', { name: '回款金额' }))
    expect(onPick).toHaveBeenCalledWith('回款金额')
  })
})

describe('ResultTable', () => {
  it('right-aligns numeric columns and flags truncation', () => {
    render(<ResultTable columns={['name', 'amount']} rows={[{ name: 'a', amount: 1284300 }]} truncated />)
    expect(screen.getByText('1,284,300')).toHaveClass('cb-r')
    expect(screen.getByText('已截断，仅返回部分行')).toBeInTheDocument()
  })
})

describe('SqlBlock', () => {
  it('only offers editing when a runner is provided', async () => {
    const onRun = vi.fn()
    const { rerender } = render(<SqlBlock sql="SELECT 1" defaultOpen />)
    expect(screen.queryByRole('button', { name: '编辑' })).toBeNull()
    expect(screen.getByText('只读 · 已校验')).toBeInTheDocument()
    rerender(<SqlBlock sql="SELECT 1" onRun={onRun} defaultOpen />)
    await userEvent.click(screen.getByRole('button', { name: '编辑' }))
    await userEvent.click(screen.getByRole('button', { name: '重新执行' }))
    expect(onRun).toHaveBeenCalledWith('SELECT 1')
  })

  it('starts folded to one line and opens on click', async () => {
    const onRun = vi.fn()
    render(<SqlBlock sql="SELECT 1" onRun={onRun} />)
    expect(screen.queryByText('1')).toBeNull()
    expect(screen.queryByRole('button', { name: '编辑' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /SQL/, expanded: false }))
    expect(screen.getByText('SELECT')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '编辑' }))
    await userEvent.click(screen.getByRole('button', { name: '重新执行' }))
    expect(onRun).toHaveBeenCalledWith('SELECT 1')
  })

  it('cancels edits, leaves editing after a run and still folds', async () => {
    const onRun = vi.fn()
    render(<SqlBlock sql="SELECT 1" onRun={onRun} defaultOpen />)
    await userEvent.click(screen.getByRole('button', { name: '编辑' }))
    await userEvent.type(screen.getByLabelText('编辑 SQL'), ' + 1')
    expect(screen.getByText('已手动修改')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.queryByLabelText('编辑 SQL')).toBeNull()
    expect(onRun).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '编辑' }))
    await userEvent.type(screen.getByLabelText('编辑 SQL'), ' + 1')
    await userEvent.click(screen.getByRole('button', { name: '重新执行' }))
    expect(onRun).toHaveBeenCalledWith('SELECT 1 + 1')
    expect(screen.queryByLabelText('编辑 SQL')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /SQL/, expanded: true }))
    expect(screen.queryByText('SELECT')).toBeNull()
  })
})

describe('Composer @ mentions', () => {
  const metrics = [{ id: 7, name: 'GMV', aliases: ['成交额'] }, { id: 8, name: '回款金额', aliases: [] }]
  it('lists metrics after @, inserts the pick and submits its id', async () => {
    const onSubmit = vi.fn()
    render(<Composer source="pg · sales" metrics={metrics} onSubmit={onSubmit} />)
    const box = screen.getByLabelText('提问')
    await userEvent.type(box, '看一下 @成')
    await userEvent.click(screen.getByRole('button', { name: /GMV/ }))
    expect(box).toHaveValue('看一下 @GMV ')
    await userEvent.type(box, '趋势{Enter}')
    expect(onSubmit).toHaveBeenCalledWith('看一下 @GMV 趋势', [7])
  })
  it('drops the id when the mention is deleted before sending', async () => {
    const onSubmit = vi.fn()
    render(<Composer source="pg · sales" metrics={metrics} onSubmit={onSubmit} />)
    const box = screen.getByLabelText('提问')
    await userEvent.type(box, '@GM')
    await userEvent.click(screen.getByRole('button', { name: /GMV/ }))
    await userEvent.clear(box)
    await userEvent.type(box, '普通问题{Enter}')
    expect(onSubmit).toHaveBeenCalledWith('普通问题', [])
  })
})

describe('result and empty-state helpers', () => {
  it('shows the total when only a preview is on screen', () => {
    render(<ResultTable columns={['id']} rows={[{ id: 1 }, { id: 2 }]} total={700} />)
    expect(screen.getByText(/700 行，显示前 2 行/)).toBeInTheDocument()
  })
  it('echoes the filter used for an empty result', async () => {
    const { EmptyResultHint } = await import('./components/chat')
    render(<EmptyResultHint where={"region = '华东区'"} />)
    expect(screen.getByText("region = '华东区'")).toBeInTheDocument()
    expect(screen.getByText('查询成功，但没有匹配到任何记录')).toBeInTheDocument()
  })
})

describe('Composer model choice', () => {
  const models = [{ id: 1, name: 'zcode/GLM-5.3-Flash' }, { id: 2, name: 'workbuddy/glm-5.3-flash' }]
  it('shows the selected model on one line and reports a change', async () => {
    const onModelChange = vi.fn()
    render(<Composer source="pg · sales" models={models} modelId={1} onModelChange={onModelChange} onSubmit={() => undefined} />)
    const trigger = screen.getByRole('combobox', { name: '模型' })
    expect(trigger).toHaveTextContent('zcode/GLM-5.3-Flash')
    await userEvent.click(trigger)
    await userEvent.click(screen.getByRole('option', { name: /workbuddy/ }))
    expect(onModelChange).toHaveBeenCalledWith(2)
  })
  it('hides the selector when no model is configured', () => {
    render(<Composer source="pg · sales" models={[]} onSubmit={() => undefined} />)
    expect(screen.queryByRole('combobox', { name: '模型' })).toBeNull()
  })
})
