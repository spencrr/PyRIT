# Explicit target outcomes

Targets may opt into `TargetResponse`, a list-compatible response with a completed,
cancelled, failed, or unknown status. Existing message-list targets are unchanged.
The prompt normalizer retains prepared requests and partial evidence, but raises
`TargetResponseUnavailableError` rather than echoing a tool-only request or
presenting a cancelled partial reply as a scorable final answer. Manual chat
records that outcome without fabricating a processing-error assistant message.
