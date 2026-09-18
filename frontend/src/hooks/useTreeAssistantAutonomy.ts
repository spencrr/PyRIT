import { useEffect, useRef, useState } from 'react'

import {
  captureAssistantPrecondition, prepareAssistantProposal, validateAutonomousProposal,
} from '@/components/ConversationTree/treeAssistant'
import { isNodeHidden, treeSemanticSignature } from '@/components/ConversationTree/treeModel'
import type { TreeAssistantGrant, TreeAssistantSequence, TreeWorkspace } from '@/types'

interface TreeAssistantAutonomyOptions {
  readonly workspace: TreeWorkspace
  readonly getWorkspace?: () => TreeWorkspace
  readonly selectedId: string | null
  readonly active: boolean
  readonly disabled: boolean
  readonly onStop?: () => void
  readonly runSequence: (work: (sequence: TreeAssistantSequence) => Promise<void>) => Promise<void>
}

type AutonomyState =
  | { readonly kind: 'off' | 'paused' | 'finished'; readonly status: string }
  | { readonly kind: 'running' | 'stopping'; readonly status: string; readonly grant: TreeAssistantGrant }

/** Optional orchestration. The session hook owns persistence and never restores this authority. */
export function useTreeAssistantAutonomy(options: TreeAssistantAutonomyOptions) {
  const [state, setState] = useState<AutonomyState>({
    kind: 'off', status: 'Autonomy is off. Restoring chat never resumes actions.',
  })
  const optionsRef = useRef(options)
  const generation = useRef(0)
  const mounted = useRef(false)
  useEffect(() => { optionsRef.current = options }, [options])
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  useEffect(() => {
    if (!options.active) {
      generation.current++
    }
  }, [options.active])

  function update(next: AutonomyState): void {
    if (mounted.current) setState(next)
  }

  function stop(): void {
    generation.current++
    optionsRef.current.onStop?.()
    setState((previous: AutonomyState) => previous.kind === 'running'
      ? { ...previous, kind: 'stopping', status: 'Stopping after in-flight work settles…' } : previous)
  }

  async function runAutonomy(goal: string, operationBudget: number): Promise<void> {
    if (!goal.trim()) return
    await optionsRef.current.runSequence(async (sequence: TreeAssistantSequence) => {
      const config = optionsRef.current
      const workspace = config.getWorkspace?.() ?? config.workspace
      const root = config.selectedId
      if (!root || !workspace.nodes.some((node) => node.id === root) || isNodeHidden(workspace, root)) throw new Error('Select a non-pruned subtree first.')
      if (!Number.isSafeInteger(operationBudget) || operationBudget < 1 || operationBudget > 100_000) throw new Error('Choose a budget between 1 and 100000 operations.')
      const run = ++generation.current
      const allowance: TreeAssistantGrant = { root_node_id: root, remaining_operations: operationBudget, remaining_turns: 10, goal: goal.trim() }
      const status = 'Running within the selected subtree. Each planning turn may use bounded tool/model calls.'
      try {
        while (mounted.current && generation.current === run && allowance.remaining_turns > 0) {
          if (optionsRef.current.disabled || !optionsRef.current.active) throw new Error('Autonomy paused because the workspace is busy, edited, or hidden.')
          const before = optionsRef.current.getWorkspace?.() ?? optionsRef.current.workspace
          const precondition = captureAssistantPrecondition(before)
          allowance.remaining_turns--
          update({ kind: 'running', grant: { ...allowance }, status })
          const turn = await sequence.request(allowance.goal, { ...allowance })
          if (!mounted.current || generation.current !== run) break
          const after = optionsRef.current.getWorkspace?.() ?? optionsRef.current.workspace
          if (treeSemanticSignature(after) !== precondition.semanticSignature || optionsRef.current.disabled || !optionsRef.current.active) {
            throw new Error('Tree changed while planning. Review the proposal manually; autonomy paused.')
          }
          const proposal = turn.proposals[0]
          if (!proposal) { update({ kind: 'finished', status: 'Finished: the assistant returned no further action.' }); return }
          const review = prepareAssistantProposal(after, proposal, precondition)
          const operations = validateAutonomousProposal(after, proposal, allowance, review)
          const receipt = await sequence.resolve(proposal, { ...allowance }, review)
          allowance.remaining_operations -= operations
          if (receipt.status !== 'applied') throw new Error('Autonomy paused after an incomplete action. Review its result before continuing.')
          if (allowance.remaining_operations <= 0) { update({ kind: 'paused', status: 'Paused: operation budget exhausted.' }); return }
          await new Promise<void>((done: () => void) => { setTimeout(done, 0) })
        }
        update({ kind: 'paused', status: generation.current !== run
          ? 'Stopped. In-flight work has settled; no further actions will run.' : 'Paused: the ten-turn planning limit was reached.' })
      } catch (failure: unknown) {
        update({ kind: 'paused', status: 'Paused. Review the reported issue and pending proposal before granting autonomy again.' })
        throw failure
      }
    })
  }

  return { state, grant: 'grant' in state ? state.grant : null, autonomyStatus: state.status, runAutonomy, stop }
}
