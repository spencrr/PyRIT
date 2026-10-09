import { makeStyles, tokens } from '@fluentui/react-components'

export const useAgentTargetDialogStyles = makeStyles({
  surface: { width: 'min(640px, calc(100vw - 32px))', maxWidth: '640px' },
  form: { gridColumn: '1 / -1', minWidth: 0 },
  content: {
    display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM,
    overflowY: 'auto', maxHeight: '65vh', paddingRight: tokens.spacingHorizontalS,
  },
  section: {
    display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS,
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`, paddingTop: tokens.spacingVerticalM,
  },
})
