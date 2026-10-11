# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Managed immutable checkpoint files beneath the execution store's ownership boundary."""

import asyncio
import hashlib
import os
import stat
from pathlib import Path
from uuid import UUID, uuid4

from pyrit.common.asyncio_task import await_task_completion_async
from pyrit.models.agent_checkpoint import AgentCheckpoint, CheckpointFile


class AgentCheckpointStore:
    """Write archives before publishing the manifest; deletion leaves no executable checkpoint."""

    MAX_BYTES = 64 * 1024 * 1024
    MAX_CHECKPOINTS = 8

    def __init__(self, root: Path) -> None:
        """Use the parent execution manager's exclusive store lock."""
        self.root = root
        self._write_lock = asyncio.Lock()
        self._reservations: set[UUID] = set()

    @staticmethod
    def describe(data: bytes) -> CheckpointFile:
        """
        Describe the exact managed bytes.

        Returns:
            CheckpointFile: Byte count and hash.
        """
        return CheckpointFile(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    async def save_async(self, *, checkpoint: AgentCheckpoint, workspace: bytes, state: bytes) -> None:
        """Publish only complete, verified archives; join writes before cancellation escapes."""
        async with self._write_lock:
            await await_task_completion_async(
                asyncio.create_task(
                    asyncio.to_thread(self._save, checkpoint=checkpoint, workspace=workspace, state=state)
                )
            )

    async def ensure_capacity_async(self) -> None:
        """Reject full retention before stopping an otherwise live execution."""
        await asyncio.to_thread(self._ensure_capacity)

    async def reserve_async(self) -> UUID:
        """
        Reserve retention before stopping the source allocation.

        Returns:
            UUID: The checkpoint identity reserved for one in-flight capture.
        """
        async with self._write_lock:
            await asyncio.to_thread(self._ensure_capacity)
            checkpoint_id = uuid4()
            self._reservations.add(checkpoint_id)
            return checkpoint_id

    async def release_reservation_async(self, checkpoint_id: UUID) -> None:
        """Release an in-flight retention slot after success or failure."""
        async with self._write_lock:
            self._reservations.discard(checkpoint_id)

    def _ensure_capacity(self) -> None:
        count = 0
        if self.root.exists():
            self._require_directory(self.root)
            count = len(list(self.root.iterdir()))
        if count + len(self._reservations) >= self.MAX_CHECKPOINTS:
            raise ValueError("Checkpoint retention capacity reached; delete an old checkpoint first")

    def _save(self, *, checkpoint: AgentCheckpoint, workspace: bytes, state: bytes) -> None:
        if self.describe(workspace) != checkpoint.workspace or self.describe(state) != checkpoint.harness_state:
            raise ValueError("Checkpoint archive integrity does not match its manifest")
        if max(len(workspace), len(state)) > self.MAX_BYTES:
            raise ValueError("Checkpoint archive exceeds the 64 MiB limit")
        self.root.mkdir(exist_ok=True)
        self._require_directory(self.root)
        if checkpoint.id not in self._reservations:
            raise ValueError("Checkpoint retention was not reserved before capture")
        directory = self.root / str(checkpoint.id)
        directory.mkdir(mode=0o700)
        try:
            for name, data in (
                ("workspace.tar", workspace),
                ("harness-state.tar", state),
                ("checkpoint.json", checkpoint.model_dump_json().encode()),
            ):
                path = directory / name
                with path.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
        except BaseException:
            self._delete(checkpoint.id)
            raise

    async def load_async(self, checkpoint_id: UUID) -> tuple[AgentCheckpoint, bytes, bytes]:
        """
        Read exact bounded files; never accept arbitrary caller archive paths.

        Returns:
            tuple[AgentCheckpoint, bytes, bytes]: Manifest, workspace, and native state.
        """
        return await await_task_completion_async(asyncio.create_task(asyncio.to_thread(self._load, checkpoint_id)))

    async def get_async(self, checkpoint_id: UUID) -> AgentCheckpoint:
        """
        Read metadata without fetching native state.

        Returns:
            AgentCheckpoint: The immutable checkpoint description.
        """
        return await await_task_completion_async(asyncio.create_task(asyncio.to_thread(self._get, checkpoint_id)))

    def _get(self, checkpoint_id: UUID) -> AgentCheckpoint:
        directory = self.root / str(checkpoint_id)
        self._require_directory(self.root)
        self._require_directory(directory)
        checkpoint = AgentCheckpoint.model_validate_json(self._read(path=directory / "checkpoint.json", maximum=65536))
        if checkpoint.id != checkpoint_id:
            raise ValueError("Checkpoint identity does not match its managed path")
        return checkpoint

    def _load(self, checkpoint_id: UUID) -> tuple[AgentCheckpoint, bytes, bytes]:
        directory = self.root / str(checkpoint_id)
        self._require_directory(self.root)
        self._require_directory(directory)
        if (directory / "deleting").exists():
            raise ValueError("Checkpoint deletion is incomplete; retry deletion, not restoration")
        checkpoint = self._get(checkpoint_id)
        workspace = self._read(path=directory / "workspace.tar", maximum=self.MAX_BYTES)
        state = self._read(path=directory / "harness-state.tar", maximum=self.MAX_BYTES)
        if self.describe(workspace) != checkpoint.workspace or self.describe(state) != checkpoint.harness_state:
            raise ValueError("Checkpoint bytes changed; refusing restoration")
        return checkpoint, workspace, state

    async def delete_async(self, checkpoint_id: UUID) -> None:
        """Remove only the named checkpoint's known files; never remove execution evidence."""
        async with self._write_lock:
            await await_task_completion_async(asyncio.create_task(asyncio.to_thread(self._delete, checkpoint_id)))

    async def deletion_pending_async(self, checkpoint_id: UUID) -> bool:
        """
        Detect an interrupted deletion even if metadata has already been removed.

        Returns:
            bool: Whether retained cleanup state still needs explicit deletion.
        """
        return await asyncio.to_thread(self._deletion_pending, checkpoint_id)

    def _deletion_pending(self, checkpoint_id: UUID) -> bool:
        directory = self.root / str(checkpoint_id)
        if not directory.exists():
            return False
        self._require_directory(self.root)
        self._require_directory(directory)
        return (directory / "deleting").exists() or not (directory / "checkpoint.json").exists()

    def _delete(self, checkpoint_id: UUID) -> None:
        directory = self.root / str(checkpoint_id)
        self._require_directory(self.root)
        self._require_directory(directory)
        marker = directory / "deleting"
        if not marker.exists():
            with marker.open("xb") as stream:
                stream.write(b"deletion requested\n")
                stream.flush()
                os.fsync(stream.fileno())
        for name in ("workspace.tar", "harness-state.tar", "checkpoint.json", "deleting"):
            path = directory / name
            if path.exists():
                self._require_regular(path)
                path.unlink()
        directory.rmdir()

    @staticmethod
    def _require_directory(path: Path) -> None:
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or path.is_symlink()
            or (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        ):
            raise ValueError("Checkpoint directory is not a regular managed directory")

    @staticmethod
    def _require_regular(path: Path) -> None:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or path.is_symlink()
            or (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        ):
            raise ValueError("Checkpoint file is not a regular managed file")

    @classmethod
    def _read(cls, *, path: Path, maximum: int) -> bytes:
        cls._require_regular(path)
        with path.open("rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("Checkpoint file exceeds its read limit")
        return data
