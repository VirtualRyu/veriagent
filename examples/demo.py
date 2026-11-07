"""Run three sharing requests over real stdio MCP in temporary synthetic state."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile

from mcp import Client, StdioServerParameters

from veriagent.authoring import bootstrap_demo
from veriagent.store import Store


async def demonstrate():
    with tempfile.TemporaryDirectory(prefix="veriagent-demo-") as directory:
        home = Path(directory)
        bootstrap_demo(home)
        store = Store(home / "demo.sqlite3")
        store.initialize(seed_demo=True)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "veriagent.server", "--home", str(home)],
        )
        requests = []
        async with Client(parameters) as client:
            for document_id, recipient in (
                ("public-guide", "alice@example.org"),
                ("confidential-plan", "alice@outside.test"),
                ("confidential-plan", "alice@example.org"),
            ):
                response = await client.call_tool("share_document", {
                    "document_id": document_id,
                    "recipient": recipient,
                })
                if response.is_error:
                    raise RuntimeError(f"Demo tool request failed for {document_id}")
                requests.append({"document_id": document_id, "recipient": recipient,
                                 **response.structured_content})
        return {"scope": "synthetic temporary-state stdio MCP demo", "requests": requests,
                "outbox_rows": len(store.inspect()["outbox"])}


def main():
    print(json.dumps(asyncio.run(demonstrate()), indent=2))


if __name__ == "__main__":
    main()
