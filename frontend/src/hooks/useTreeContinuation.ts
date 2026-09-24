import { useEffect, useEffectEvent, useState } from 'react'

import type { TreeContinuation, TreeWorkspace } from '@/types'
import { discoverTreeContinuation } from '@/components/ConversationTree/treeHistory'
import { getCurrentAttemptId } from '@/components/ConversationTree/treeModel'

export function useTreeContinuation(workspace: TreeWorkspace | null, nodeId: string | null, active: boolean, running: boolean) {
  const [refresh, setRefresh] = useState(0)
  const [result, setResult] = useState<{ key: string; continuation?: TreeContinuation; error?: string } | null>(null)
  const node = workspace?.nodes.find((item) => item.id === nodeId)
  const key = node && workspace ? `${workspace.id}:${node.id}:${getCurrentAttemptId(node)}` : ''
  const check = useEffectEvent(() => {
    if (!workspace || !node?.conversationId || node.status === 'running') return undefined
    return discoverTreeContinuation(workspace, node.id)
  })
  useEffect(() => {
    if (!active || running) return
    let cancelled = false
    const request = check()
    if (!request) return
    request.then((continuation) => {
      if (!cancelled) setResult({ key, continuation })
    }).catch((error: unknown) => {
      if (!cancelled) setResult({ key, error: error instanceof Error ? error.message : 'Could not check backend history.' })
    })
    return () => { cancelled = true }
  }, [key, active, running, refresh])
  useEffect(() => {
    if (!active) return
    const focus = () => { setRefresh((previous) => previous + 1) }
    window.addEventListener('focus', focus)
    return () => { window.removeEventListener('focus', focus) }
  }, [active])
  return { ...((result?.key === key && active) ? result : {}), refresh: () => { setRefresh((previous) => previous + 1) } }
}
