const timestamp = new Intl.DateTimeFormat('en-GB', {
  dateStyle: 'medium', timeStyle: 'medium', timeZone: 'UTC',
})

export function SourceTime({ value }: { value: string | null }) {
  return value ? <time dateTime={value}>{timestamp.format(new Date(value))} UTC</time> : <>Not available</>
}
