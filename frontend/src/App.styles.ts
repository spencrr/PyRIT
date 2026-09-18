import { makeStyles } from '@fluentui/react-components'

export const useAppStyles = makeStyles({
  treeWorkspace: {
    display: 'flex',
    flex: 1,
    minWidth: 0,
    minHeight: 0,
    '&[hidden]': { display: 'none' },
  },
})
