import type { CascaderProps, SelectProps } from 'antd'

function normalize(value: string) {
  return value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase().trim()
}

export function fuzzyMatch(input: string, candidate: string) {
  const query = normalize(input).replace(/[\s·→/\-_.]+/g, '')
  const text = normalize(candidate).replace(/[\s·→/\-_.]+/g, '')
  if (!query) return true
  let cursor = 0
  for (const character of text) {
    if (character === query[cursor]) cursor += 1
    if (cursor === query.length) return true
  }
  return false
}

export const fuzzySelectFilter: NonNullable<SelectProps['filterOption']> = (input, option) =>
  fuzzyMatch(input, `${option?.label ?? ''} ${option?.value ?? ''}`)

export const fuzzyCascaderFilter: NonNullable<CascaderProps['showSearch']> extends infer Search
  ? Search extends { filter?: infer Filter } ? NonNullable<Filter> : never
  : never = (input, path) => fuzzyMatch(input, path.map(option => String(option.label ?? '')).join(' '))
