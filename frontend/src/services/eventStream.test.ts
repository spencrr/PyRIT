import { ReadableStream } from 'node:stream/web'
import { consumeEventStream } from './eventStream'

describe('consumeEventStream', () => {
  it('decodes split frames and UTF-8 without treating heartbeats as events', async () => {
    const encoded = new TextEncoder().encode(': heartbeat\n\nevent: state\ndata: {"text":"é"}\n\nevent: end\ndata: {}\n\n')
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const byte of encoded) controller.enqueue(new Uint8Array([byte]))
        controller.close()
      },
    })
    const onFrame = jest.fn().mockResolvedValue(undefined)
    await consumeEventStream({ body } as Response, onFrame)
    expect(onFrame.mock.calls.map((call) => call[0])).toEqual([
      { event: 'state', data: '{"text":"é"}' }, { event: 'end', data: '{}' },
    ])
  })
})
