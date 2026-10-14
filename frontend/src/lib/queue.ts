/** 同时最多运行 size 个任务，其余按提交顺序排队。 */
export function createQueue(size: number) {
  let active = 0
  const waiting: Array<() => void> = []
  return async function run<T>(task: () => Promise<T>): Promise<T> {
    if (active >= size) await new Promise<void>((resolve) => waiting.push(resolve))
    else active += 1
    try {
      return await task()
    } finally {
      // 交给下一个等待者时名额直接转交，不减少。
      if (waiting.length) waiting.shift()?.()
      else active -= 1
    }
  }
}

/** 第 n 次重试前等多久：1、2、4 秒，最多 8 秒。 */
export const backoff = (attempt: number) => Math.min(1000 * 2 ** attempt, 8000)
