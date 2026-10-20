import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { FEATURES } from './features'

/**
 * 「近 30 天引用」的数据来源（FEATURES.metricRefs 打开时才请求）。
 * 约定的新接口：GET /api/metric/refs?datasource_id=<id>&days=30
 *   -> [{ metric_id: number, count: number }]（只列被引用过的指标，没出现的按 0 次算）。
 * 接口还没上线时请求失败，按「没有数据」处理，列里显示 —。
 */
export function useMetricRefs(datasourceId: number | null) {
  return useQuery({
    queryKey: ['metric-refs', datasourceId],
    queryFn: async () => {
      const rows = await api<{ metric_id: number; count: number }[]>('GET', '/api/metric/refs', { query: { datasource_id: datasourceId, days: 30 } })
      return new Map(rows.map((r) => [r.metric_id, r.count]))
    },
    enabled: FEATURES.metricRefs && datasourceId != null,
    retry: false,
  })
}
