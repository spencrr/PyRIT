# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Benign two-turn smoke test with an independent artifact oracle."""

import argparse
import asyncio
import io
import json
import sys
from pathlib import Path
from uuid import uuid4

from pyrit.agent.execution_manager import AgentExecutionManager
from pyrit.models import MessagePiece
from pyrit.models.agent_execution import AgentTargetConfiguration, EnvironmentTemplate, HarnessProfile
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import AgentTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async


async def run_async(args: argparse.Namespace) -> None:
    """Run two real dispatch turns and verify the retained receipt outside the agent workspace."""
    root = Path(__file__).resolve().parents[1]
    command = ("copilot", "--acp", "--stdio")
    if args.scripted:
        command = (sys.executable, str(root / "tests" / "integration" / "agent" / "scripted_acp_agent.py"))
    configuration = AgentTargetConfiguration(
        name="workspace-receipt",
        harness_profile=HarnessProfile(
            command=command,
            credential_env=tuple(args.credential_env),
            authentication_method=None if args.scripted else "copilot-login",
            permission_policy="allow_once",
        ),
        environment_template=EnvironmentTemplate(
            environment="local" if args.local or args.scripted else "docker",
            local_execution_acknowledged=args.local or args.scripted,
            image=args.image,
            fixture_directory=str(root / "assets" / "agent_receipt"),
        ),
        artifact_paths=("receipt.json", "orders.json"),
    )
    await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_files=[], silent=True)
    async with AgentExecutionManager(root=Path(args.results).resolve()) as manager:
        target = AgentTarget(agent_configuration=configuration)
        target.bind_execution_manager(manager)
        normalizer = PromptNormalizer()
        conversation_id = str(uuid4())
        prompts = (
            "Read orders.json. Write receipt.json as a JSON object with a numeric total field. Report the total.",
            "Add one orange to the order in orders.json and update receipt.json. Report the new total.",
        )
        for prompt in prompts:
            response = await normalizer.send_prompt_async(
                target=target,
                conversation_id=conversation_id,
                message=MessagePiece(role="user", original_value=prompt).to_message(),
            )
            print(response.get_piece().converted_value)
        await target.reset_conversation_async(conversation_id=conversation_id)
        record = next(record for record in manager.records.values() if record.conversation_id == conversation_id)
        receipt_path = manager.store.directory(record.id) / "artifacts" / "receipt.json"
        receipt = await asyncio.to_thread(receipt_path.read_text, encoding="utf-8")
        if json.loads(receipt) != {"total": 36}:
            raise AssertionError(f"Independent receipt verification failed: {receipt}")
        fresh_id = str(uuid4())
        await normalizer.send_prompt_async(
            target=target,
            conversation_id=fresh_id,
            message=MessagePiece(role="user", original_value=prompts[0]).to_message(),
        )
        await target.reset_conversation_async(conversation_id=fresh_id)
        fresh = next(record for record in manager.records.values() if record.conversation_id == fresh_id)
        fresh_receipt = await asyncio.to_thread(
            (manager.store.directory(fresh.id) / "artifacts" / "receipt.json").read_text, encoding="utf-8"
        )
        if json.loads(fresh_receipt) != {"total": 31}:
            raise AssertionError("A fresh execution inherited mutated workspace state")
        print(f"PASS: two-turn total 36; fresh execution total 31. Evidence: {manager.store.root}")


def main() -> None:
    """Parse explicit execution mode and credential references."""
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--scripted", action="store_true", help="Use the deterministic test peer, not a model")
    mode.add_argument("--local", action="store_true", help="Run Copilot locally: NOT sandboxed")
    parser.add_argument("--image", default="pyrit-copilot-receipt:1.0.93")
    parser.add_argument("--credential-env", action="append", default=[])
    parser.add_argument("--results", required=True, help="Private evidence directory owned by this demo")
    asyncio.run(run_async(parser.parse_args()))


if __name__ == "__main__":
    main()
