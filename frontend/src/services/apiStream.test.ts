import { openApiEventStream } from './api'
import { compatibility, COMPATIBILITY_HEADER } from './compatibility'

jest.mock('./compatibility', () => ({
  COMPATIBILITY_HEADER: 'PyRIT-Compatibility-ID',
  compatibility: { bundledId: `1.2.0.dev0+g${'a'.repeat(40)}`, assertReady: jest.fn(), block: jest.fn() },
}))

function response(status: number, data?: unknown, contentType = 'application/problem+json') {
  return {
    ok: status >= 200 && status < 300, status,
    headers: { get: jest.fn(() => contentType) },
    body: { cancel: jest.fn().mockResolvedValue(undefined) },
    json: jest.fn().mockResolvedValue(data),
  }
}

describe('openApiEventStream', () => {
  const originalFetch = global.fetch
  const fetchMock = jest.fn()

  beforeEach(() => {
    jest.resetAllMocks()
    global.fetch = fetchMock
  })
  afterAll(() => { global.fetch = originalFetch })

  it('sends the bundled compatibility ID and leaves successful streams unconsumed', async () => {
    const stream = response(200, undefined, 'text/event-stream')
    fetchMock.mockResolvedValue(stream)
    const signal = new AbortController().signal
    expect(await openApiEventStream('/attacks/a/conversations/c/execution/stream?after=4', signal)).toBe(stream)
    expect(fetchMock).toHaveBeenCalledWith('http://localhost:8000/api/attacks/a/conversations/c/execution/stream?after=4', {
      signal, headers: {
        Accept: 'text/event-stream', 'X-Request-ID': expect.any(String),
        [COMPATIBILITY_HEADER]: compatibility.bundledId,
      },
    })
    expect(compatibility.assertReady).toHaveBeenCalledTimes(2)
    expect(stream.json).not.toHaveBeenCalled()
    expect(stream.body.cancel).not.toHaveBeenCalled()
  })

  it.each([1, 2])('does not connect when compatibility blocks at readiness check %i', async (check) => {
    if (check === 2) jest.mocked(compatibility.assertReady).mockImplementationOnce(() => {})
    jest.mocked(compatibility.assertReady).mockImplementationOnce(() => { throw new Error('Build not ready') })
    await expect(openApiEventStream('/stream', new AbortController().signal)).rejects.toThrow('Build not ready')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('retains compatibility on the one permitted authentication retry', async () => {
    const unauthorized = response(401)
    fetchMock.mockResolvedValueOnce(unauthorized).mockResolvedValueOnce(response(200))
    await openApiEventStream('/stream', new AbortController().signal)
    expect(unauthorized.body.cancel).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    for (const [, options] of fetchMock.mock.calls) {
      expect(options.headers[COMPATIBILITY_HEADER]).toBe(compatibility.bundledId)
    }
  })

  it.each([
    [400, 'urn:pyrit:compatibility:invalid'],
    [409, 'urn:pyrit:compatibility:mismatch'],
  ])('blocks incompatible streams on HTTP %i and preserves the problem detail', async (status, type) => {
    fetchMock.mockResolvedValue(response(Number(status), {
      type, detail: 'Reload the matching frontend and backend.', expected: 'server-id', actual: 'client-id',
    }))
    await expect(openApiEventStream('/stream', new AbortController().signal))
      .rejects.toThrow(`Execution stream failed (${status}): Reload the matching frontend and backend.`)
    expect(compatibility.block).toHaveBeenCalledWith(
      'The backend rejected this frontend build identity.', 'server-id', 'client-id',
    )
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('shows actionable non-compatibility errors without blocking the build', async () => {
    fetchMock.mockResolvedValue(response(503, { detail: 'Runtime is unavailable; repair configuration.' }))
    await expect(openApiEventStream('/stream', new AbortController().signal)).rejects.toThrow('repair configuration')
    expect(compatibility.block).not.toHaveBeenCalled()
  })

  it('does not expose proxy HTML or malformed JSON in the UI', async () => {
    const html = response(502, '<html>private proxy diagnostic</html>', 'text/html')
    fetchMock.mockResolvedValueOnce(html)
    await expect(openApiEventStream('/stream', new AbortController().signal)).rejects.toThrow('Server error (502)')
    expect(html.body.cancel).toHaveBeenCalled()
    expect(html.json).not.toHaveBeenCalled()
    const malformed = response(500)
    malformed.json.mockRejectedValue(new SyntaxError('private malformed data'))
    fetchMock.mockResolvedValueOnce(malformed)
    await expect(openApiEventStream('/stream', new AbortController().signal)).rejects.toThrow('Server error (500)')
  })
})
