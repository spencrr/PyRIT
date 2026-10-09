import type { AgentStreamFrame } from '@/types'

export async function consumeEventStream(
  response: Response, onFrame: (frame: AgentStreamFrame) => Promise<void>,
): Promise<void> {
  if (!response.body) throw new Error('Event stream has no body')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffered = ''
  try {
    while (true) {
      const chunk = await reader.read()
      if (chunk.done) break
      buffered += decoder.decode(chunk.value, { stream: true })
      if (buffered.length > 32 * 1024 * 1024) throw new Error('Execution event frame exceeds the display budget')
      let boundary: number
      while ((boundary = buffered.indexOf('\n\n')) >= 0) {
        const frame = buffered.slice(0, boundary)
        buffered = buffered.slice(boundary + 2)
        let event = 'message'
        const data: string[] = []
        for (const line of frame.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim()
          if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
        }
        if (data.length) await onFrame({ event, data: data.join('\n') })
      }
    }
  } finally {
    await reader.cancel().catch((error: unknown) => {
      if (!(error instanceof Error && error.name === 'AbortError')) console.error('Could not close event reader', error)
    })
    reader.releaseLock()
  }
}
