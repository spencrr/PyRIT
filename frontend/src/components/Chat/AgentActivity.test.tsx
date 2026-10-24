import type { ReactNode } from 'react'

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import type { AgentApproval, AgentTurn } from '@/types'

import AgentActivity from './AgentActivity'

function TestWrapper({ children }: { children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

const turn: AgentTurn = {
  id: 'turn', request_id: 'request', status: 'completed', response_text: '', capture_complete: true, error: null,
}

describe('AgentActivity', () => {
  it('shows failed model inference even when the harness completed with an error reply', () => {
    render(<TestWrapper><AgentActivity turn={{ ...turn, response_text: 'Provider could not be reached' }}
      activity={{ text: '', tools: [], inference: {
        request: { status: 'failed', target: 'target', bytes: 0, error: 'Check the target endpoint and backend DNS.' },
      } }} /></TestWrapper>)
    expect(screen.getByText(/Check the target endpoint and backend DNS.*Harness completion does not mean/)).toBeVisible()
  })

  it('reports earlier model failures without declaring a recovered turn failed', () => {
    render(<TestWrapper><AgentActivity turn={{ ...turn, response_text: 'Recovered answer' }}
      activity={{ text: '', tools: [], inference: {
        first: { status: 'failed' }, second: { status: 'completed', bytes: 42 },
      } }} /></TestWrapper>)
    expect(screen.getByText(/1 model request\(s\) failed during this turn/)).toBeVisible()
    expect(screen.getByText('completed', { exact: true })).toBeVisible()
  })

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

  it('distinguishes display reports from canonical scoring evidence', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><AgentActivity turn={{ ...turn, tool_call_messages: 2,
      tool_call_omissions: { unknown: 'name_not_reported' } }} activity={{
      text: '', tools: [
        { id: 'known', title: 'Lookup', name: 'lookup', status: 'completed', firstSeen: '', lastSeen: '' },
        { id: 'unknown', title: 'Display only', status: 'completed', firstSeen: '', lastSeen: '' },
      ],
    }} /></TestWrapper>)
    await user.click(screen.getByText('Lookup — completed'))
    await user.click(screen.getByText('Display only — completed'))
    expect(screen.getByText(/Scoring evidence: canonical tool messages/)).toBeVisible()
    expect(screen.getByText(/Scoring evidence: name not reported/)).toBeVisible()
    expect(screen.getByText(/Missing tool calls are undetermined, not false/)).toBeInTheDocument()
  })

  it('keeps streamed evidence visible after a transport failure without a final reply', () => {
    render(<TestWrapper><AgentActivity turn={{ ...turn, status: 'unknown', capture_complete: false }}
      activity={{ text: 'Last observed output', tools: [] }} /></TestWrapper>)
    expect(screen.getByText('Retained streamed output (not a completed reply)')).toBeInTheDocument()
    expect(screen.getByText('Last observed output')).toBeInTheDocument()
  })

  it('offers one-time decisions for pending approvals and reports settled ones', async () => {
    const user = userEvent.setup()
    const onDecision = jest.fn().mockResolvedValue(undefined)
    const approvals: AgentApproval[] = [
      { id: 'pending', turn_id: 'turn', tool_call_id: 'tool', title: 'Read orders', expires_at: '2099-01-01T00:00:00Z',
        decision: null, option_id: null, actor: null,
        options: [{ option_id: 'allow', name: 'Allow once', kind: 'allow_once' }, { option_id: 'reject', name: 'Reject', kind: 'reject_once' }] },
      { id: 'settled', turn_id: 'turn', tool_call_id: 'other', title: 'Write receipt', expires_at: '2099-01-01T00:00:00Z',
        decision: 'operator_deny', option_id: 'reject', actor: 'operator', options: [] },
      { id: 'elsewhere', turn_id: 'another-turn', tool_call_id: 'tool', title: 'Not this turn', expires_at: '2099-01-01T00:00:00Z',
        decision: null, option_id: null, actor: null, options: [] },
    ]
    render(<TestWrapper><AgentActivity turn={{ ...turn, status: 'running', capture_complete: false }}
      approvals={approvals} onDecision={onDecision} activity={{ text: '', tools: [{
        id: 'tool', title: 'Read orders', status: 'pending', firstSeen: '', lastSeen: '',
      }] }} /></TestWrapper>)
    expect(screen.getByText(/Harness requested approval\. Expires /)).toBeInTheDocument()
    expect(screen.getByText('Permission: operator deny · operator')).toBeInTheDocument()
    expect(screen.queryByText('Not this turn')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Allow once' }))
    await user.click(screen.getByRole('button', { name: 'Deny' }))
    expect(onDecision.mock.calls).toEqual([['pending', true], ['pending', false]])
  })

  it('cannot allow a request the harness offered no one-time allow option for', () => {
    render(<TestWrapper><AgentActivity turn={turn} onDecision={jest.fn()} approvals={[
      { id: 'pending', turn_id: 'turn', tool_call_id: 'tool', title: 'Always allow?', expires_at: '2099-01-01T00:00:00Z',
        decision: null, option_id: null, actor: null,
        options: [{ option_id: 'always', name: 'Allow always', kind: 'allow_always' }] },
    ]} /></TestWrapper>)
    expect(screen.getByRole('button', { name: 'Allow once' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Deny' })).toBeEnabled()
  })
})
