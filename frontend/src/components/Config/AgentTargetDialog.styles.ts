import { makeStyles, tokens } from '@fluentui/react-components'

export const useAgentTargetDialogStyles = makeStyles({
  surface: { width: '72rem', maxWidth: `calc(100vw - ${tokens.spacingHorizontalXXL})` },
  form: { gridColumn: '1 / -1', minWidth: 0 },
  content: {
    display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM,
    overflowY: 'auto', maxHeight: '65vh', paddingRight: tokens.spacingHorizontalS,
  },
  section: {
    minWidth: 0,
    display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS,
    border: `1px solid ${tokens.colorNeutralStroke2}`, borderRadius: tokens.borderRadiusMedium,
    padding: tokens.spacingHorizontalM,
  },
  composition: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 17rem), 1fr))',
    alignItems: 'start',
    gap: tokens.spacingHorizontalM,
  },
  select: {
    minWidth: 0,
    width: '100%',
    maxWidth: '100%',
  },
})
