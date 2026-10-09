import type { ReactNode } from 'react'

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import type { AgentTurn } from '@/types'

import AgentActivity from './AgentActivity'

function TestWrapper({ children }: { children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

const turn: AgentTurn = {
  id: 'turn', request_id: 'request', status: 'completed', response_text: '', capture_complete: true, error: null,
}

describe('AgentActivity', () => {
  it('shows tool-only completion and expandable agent-reported evidence', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><AgentActivity turn={turn} activity={{ text: '', tools: [{
      id: 'tool', title: 'Write receipt', status: 'completed',
      firstSeen: '2026-10-08T00:00:00Z', lastSeen: '2026-10-08T00:00:01Z', rawOutput: { total: 31 },
    }] }} /></TestWrapper>)
    expect(screen.getByText(/Completed without a text reply/)).toBeInTheDocument()
    await user.click(screen.getByText('Write receipt — completed'))
    expect(screen.getByText(/Tool name: not reported/)).toBeVisible()
    expect(screen.getByText(/"total": 31/)).toBeVisible()
  })

  it('does not claim cancelled effects were rolled back or missing evidence complete', () => {
    render(<TestWrapper><AgentActivity turn={{ ...turn, status: 'cancelled', capture_complete: false }} /></TestWrapper>)
    expect(screen.getByText(/Earlier tool effects are not rolled back/)).toBeInTheDocument()
    expect(screen.getByText(/Capture is incomplete/)).toBeInTheDocument()
  })

  it('keeps streamed evidence visible after a transport failure without a final reply', () => {
    render(<TestWrapper><AgentActivity turn={{ ...turn, status: 'unknown', capture_complete: false }}
      activity={{ text: 'Last observed output', tools: [] }} /></TestWrapper>)
    expect(screen.getByText('Retained streamed output (not a completed reply)')).toBeInTheDocument()
    expect(screen.getByText('Last observed output')).toBeInTheDocument()
  })
})
