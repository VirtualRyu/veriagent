"""Actual MCP startup and fail-closed behavior without a fixture pack."""
import asyncio
import sys

from mcp import Client, StdioServerParameters


def test_empty_state_mcp_starts_and_cannot_execute(tmp_path):
    from veriagent.store import Store

    async def check():
        parameters = StdioServerParameters(command=sys.executable,
            args=["-m", "veriagent.server", "--home", str(tmp_path)])
        async with Client(parameters) as client:
            documents = await client.call_tool("list_documents", {})
            assert not documents.is_error
            assert documents.structured_content["result"] == []
            response = await client.call_tool("share_document", {
                "document_id": "missing-document", "recipient": "reader@example.invalid"})
            assert not response.is_error
            assert response.structured_content["decision"] == "HUMAN_REVIEW"
            assert response.structured_content["executed"] is False
        assert Store(tmp_path / "demo.sqlite3").inspect()["outbox"] == []

    asyncio.run(check())
