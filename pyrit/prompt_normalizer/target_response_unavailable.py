# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Explicit non-message outcomes at the message-oriented normalizer boundary."""

from pyrit.models.target_response import TargetResponse


class TargetResponseUnavailableError(ValueError):
    """The operation is recorded but cannot supply a completed message for an attack/scorer."""

    def __init__(self, outcome: TargetResponse) -> None:
        """Retain the real outcome instead of fabricating a processing-error response."""
        self.outcome = outcome
        super().__init__(
            f"Target operation {outcome.status.value} has no completed scorable response. "
            "Inspect its execution evidence; do not automatically resend the operation."
        )
