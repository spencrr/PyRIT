# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Bounded Docker tar validation without host extraction."""

import io
import tarfile
from pathlib import PurePosixPath


class CheckpointArchive:
    """Accept only ordinary state under a declared root; reject links and special files."""

    MAX_BYTES = 64 * 1024 * 1024
    MAX_FILES = 2000

    @classmethod
    def normalize(cls, *, raw: bytes, root: str, secrets: tuple[str, ...] = (), portable: bool = False) -> bytes:
        """
        Validate and produce an archive rooted for copying into a fresh container.

        Returns:
            bytes: Safe tar entries with original file ownership.

        Raises:
            ValueError: Entries are unsafe, oversized, or contain a runtime credential.
        """
        if len(raw) > cls.MAX_BYTES:
            raise ValueError("Checkpoint archive exceeds the 64 MiB limit")
        prefix = PurePosixPath(root.lstrip("/"))
        expected = prefix if portable else PurePosixPath(prefix.name)
        output = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(raw)) as source, tarfile.open(fileobj=output, mode="w") as target:
            names: set[str] = set()
            total = 0
            for index, entry in enumerate(source):
                path = PurePosixPath(entry.name)
                if (
                    index >= cls.MAX_FILES
                    or path.is_absolute()
                    or ".." in path.parts
                    or "\\" in entry.name
                    or ":" in entry.name
                    or not path.is_relative_to(expected)
                    or str(path) in names
                    or not (entry.isfile() or entry.isdir())
                    or entry.size < 0
                ):
                    raise ValueError("Checkpoint has an unsafe, duplicate, or excessive archive entry")
                total += entry.size + 1024
                if total > cls.MAX_BYTES:
                    raise ValueError("Checkpoint contents exceed the byte limit")
                names.add(str(path))
                content = source.extractfile(entry) if entry.isfile() else None
                entry.name = str(prefix / path.relative_to(expected))
                entry.pax_headers = {}
                target.addfile(entry, content)
        data = output.getvalue()
        if not names or len(data) > cls.MAX_BYTES:
            raise ValueError("Checkpoint archive is empty or oversized")
        if any(secret and secret.encode() in data for secret in secrets):
            raise ValueError("Checkpoint contains a runtime credential; refusing to retain it")
        return data

    @staticmethod
    def parents(*, root: str, uid: int, gid: int) -> bytes:
        """
        Prepare only native-state ancestor directories with the image user's ownership.

        Returns:
            bytes: Directory-only tar for a fresh private home.
        """
        home = PurePosixPath("home/agent")
        state = PurePosixPath(root.lstrip("/"))
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            for parent in reversed(state.parents):
                if parent == home or not parent.is_relative_to(home):
                    continue
                entry = tarfile.TarInfo(str(parent))
                entry.type, entry.mode, entry.uid, entry.gid = tarfile.DIRTYPE, 0o700, uid, gid
                archive.addfile(entry)
        return output.getvalue()
