import type { CSSProperties } from 'react'
import { theme } from 'antd'

export const antdTheme = {
  algorithm: theme.defaultAlgorithm,
  token: {
    colorPrimary: '#1557A8',
    colorInfo: '#1557A8',
    colorSuccess: '#24734C',
    colorWarning: '#8A5B00',
    colorError: '#AE302B',
    colorTextBase: '#172B45',
    colorBgLayout: '#F3F6FA',
    colorBgContainer: '#FFFFFF',
    colorBorder: '#D8E0EA',
    colorBorderSecondary: '#E6EBF1',
    borderRadius: 8,
  },
}

export type StatusTone = 'info' | 'success' | 'warning' | 'error'

export const statusTagStyles: Record<StatusTone, CSSProperties> = {
  info: { color: '#174A82', backgroundColor: '#EAF2FC', borderColor: '#C5D8F0' },
  success: { color: '#185C3A', backgroundColor: '#EAF5EF', borderColor: '#B9DEC9' },
  warning: { color: '#755000', backgroundColor: '#FFF5DE', borderColor: '#EAD59A' },
  error: { color: '#9B2E29', backgroundColor: '#FCEDEC', borderColor: '#EBC5C2' },
}
