export function readable(value: string): string {
  const text = value.replaceAll('_', ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return 'Unavailable'
  if (seconds === 0) return '0s'
  if (seconds < 1) return '<1s'
  const rounded = Math.round(seconds)
  if (rounded >= 3600) return `${Math.floor(rounded / 3600)}h ${Math.floor((rounded % 3600) / 60)}m`
  if (rounded >= 60) return `${Math.floor(rounded / 60)}m ${rounded % 60}s`
  return `${rounded}s`
}
