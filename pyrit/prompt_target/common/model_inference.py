# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Optional target-owned single-inference contract, preserving provider response payloads."""

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class InferenceResponse:
    """Raw provider outcome; parsing for observation must not change the harness's payload."""

    status_code: int
    headers: Mapping[str, str]
    body: AsyncIterator[bytes]


class InferenceAdmissionError(RuntimeError):
    """A bounded inference admission wait expired before contacting the provider."""
