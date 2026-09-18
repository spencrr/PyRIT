import { useRef, useState } from 'react'

import { Button, Field, Input, Popover, PopoverSurface, PopoverTrigger, Text } from '@fluentui/react-components'

import type { TreeCommand } from '@/types'

import { useConversationTreeStyles } from './ConversationTree.styles'
import { MAX_FAN_OUT } from './treeModel'

interface TreeSampleControlProps {
  nodeId: string
  disabled: boolean
  autoRun: boolean
  onCommand: (command: TreeCommand) => Promise<boolean>
}

export default function TreeSampleControl({ nodeId, disabled: externallyDisabled, autoRun, onCommand }: TreeSampleControlProps) {
  const styles = useConversationTreeStyles()
  const [open, setOpen] = useState(false)
  const [count, setCount] = useState('10')
  const [submitting, setSubmitting] = useState(false)
  const pending = useRef(false)
  const disabled = externallyDisabled || submitting
  const amount = Number(count)
  const valid = Number.isInteger(amount) && amount >= 1 && amount <= MAX_FAN_OUT
  async function submit(): Promise<void> {
    if (pending.current || !valid) return
    pending.current = true
    setSubmitting(true)
    try { if (await onCommand({ type: 'sample', nodeId, count: amount })) setOpen(false) }
    finally { pending.current = false; setSubmitting(false) }
  }
  return (
    <Popover open={open && !disabled} onOpenChange={(_, data) => { setOpen(data.open) }}>
      <PopoverTrigger disableButtonEnhancement>
        <Button className={styles.button} disabled={disabled}>Sample again</Button>
      </PopoverTrigger>
      <PopoverSurface><div className={styles.stack}>
        <Text>Independent samples of this saved prompt and pipeline.</Text>
        <Field label="Additional attempts" validationState={valid ? 'none' : 'error'} validationMessage={!valid ? `Choose 1-${MAX_FAN_OUT} attempts.` : undefined}>
          <Input type="number" min={1} max={MAX_FAN_OUT} value={count} onChange={(_, data) => { setCount(data.value) }} />
        </Field>
        <div className={styles.row}>{[2, 5, 10].map((number) =>
          <Button className={styles.button} key={number} aria-label={`${number} additional attempts`} onClick={() => { setCount(String(number)) }}>{number}</Button>)}</div>
        <Button className={styles.button} appearance="primary" disabled={!valid || disabled} onClick={() => { void submit() }}>
          {autoRun ? 'Add & run' : 'Add'} {valid ? amount : ''} samples
        </Button>
      </div></PopoverSurface>
    </Popover>
  )
}
