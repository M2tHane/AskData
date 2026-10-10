const COPIED = ['fill', 'stroke', 'stroke-width', 'stroke-linejoin', 'font-family', 'font-size', 'font-weight', 'text-anchor', 'opacity']

export const HEADER_HEIGHT = 36

/** 把页面上的 SVG 图表连同标题画成 PNG。样式来自设计系统的 CSS 变量，导出前先写成内联值。 */
export async function chartToPng(svg: SVGSVGElement, title: string, scale = 2): Promise<Blob> {
  const clone = svg.cloneNode(true) as SVGSVGElement
  const live = svg.querySelectorAll('*')
  const copy = clone.querySelectorAll<SVGElement>('*')
  live.forEach((el, i) => {
    const style = getComputedStyle(el)
    const target = copy[i]
    if (target) COPIED.forEach((p) => target.style.setProperty(p, style.getPropertyValue(p)))
  })
  const box = svg.viewBox.baseVal
  const width = box.width || svg.clientWidth
  const height = box.height || svg.clientHeight
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  clone.setAttribute('width', String(width))
  clone.setAttribute('height', String(height))
  const markup = new XMLSerializer().serializeToString(clone)
  const url = URL.createObjectURL(new Blob([markup], { type: 'image/svg+xml;charset=utf-8' }))
  try {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const img = new Image()
      img.onload = () => resolve(img)
      img.onerror = () => reject(new Error('图表渲染失败'))
      img.src = url
    })
    const root = getComputedStyle(document.documentElement)
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(width * scale)
    canvas.height = Math.round((height + HEADER_HEIGHT) * scale)
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('当前浏览器不支持导出图片')
    ctx.scale(scale, scale)
    ctx.fillStyle = root.getPropertyValue('--surface').trim() || 'white'
    ctx.fillRect(0, 0, width, height + HEADER_HEIGHT)
    ctx.fillStyle = root.getPropertyValue('--ink').trim() || 'black'
    ctx.font = `600 14px ${getComputedStyle(document.body).fontFamily}`
    ctx.textBaseline = 'middle'
    ctx.fillText(title, 12, HEADER_HEIGHT / 2)
    ctx.drawImage(image, 0, HEADER_HEIGHT, width, height)
    return await new Promise<Blob>((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('图片生成失败'))), 'image/png'))
  } finally {
    URL.revokeObjectURL(url)
  }
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
