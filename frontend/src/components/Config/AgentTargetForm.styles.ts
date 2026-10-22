import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useAgentTargetFormStyles = makeStyles({
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
  settingsFields: {
    minWidth: 0, border: 0, margin: 0, padding: 0,
    display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS,
  },
  presetAction: {
    ...mobileTouchTarget,
    flexShrink: 0,
    alignSelf: 'flex-start',
  },
  toolbar: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: tokens.spacingHorizontalS },
  actions: {
    gridColumn: '1 / -1',
    display: 'flex', justifyContent: 'flex-end', gap: tokens.spacingHorizontalS, marginTop: tokens.spacingVerticalS,
    '& button': { ...mobileTouchTarget },
    [NARROW_VIEWPORT_QUERY]: { flexWrap: 'wrap' },
  },
})
