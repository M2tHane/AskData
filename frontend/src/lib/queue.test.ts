import { describe, expect, it } from 'vitest'
import { backoff, createQueue } from './queue'

const tick = () => new Promise((r) => setTimeout(r, 0))

describe('createQueue', () => {
  it('runs at most size tasks at once, in order, and survives failures', async () => {
    const run = createQueue(2)
    let active = 0
    let peak = 0
    const order: number[] = []
    const gates: Array<() => void> = []
    const task = (n: number, fail = false) => () =>
      new Promise<number>((resolve, reject) => {
        active += 1
        peak = Math.max(peak, active)
        order.push(n)
        gates.push(() => {
          active -= 1
          if (fail) reject(new Error('x'))
          else resolve(n)
        })
      })
    const done = Promise.allSettled([run(task(1, true)), run(task(2)), run(task(3)), run(task(4))])
    await tick()
    expect(order).toEqual([1, 2])
    gates.shift()?.()
    await tick()
    expect(order).toEqual([1, 2, 3])
    while (gates.length) {
      gates.shift()?.()
      await tick()
    }
    const settled = await done
    expect(settled.map((s) => s.status)).toEqual(['rejected', 'fulfilled', 'fulfilled', 'fulfilled'])
    expect(order).toEqual([1, 2, 3, 4])
    expect(peak).toBe(2)
    // 名额全部归还：再来两个可以立刻同时开始。
    const more = [run(task(5)), run(task(6))]
    await tick()
    expect(order.slice(-2)).toEqual([5, 6])
    gates.splice(0).forEach((g) => g())
    await Promise.all(more)
  })

  it('backs off 1, 2, 4 then caps at 8 seconds', () => {
    expect([0, 1, 2, 3, 6].map(backoff)).toEqual([1000, 2000, 4000, 8000, 8000])
  })
})
