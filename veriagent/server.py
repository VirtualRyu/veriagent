"""Local stdio MCP adapter. All diagnostic output belongs on stderr."""
import os
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .models import DocumentSummary
from .policy import load_snapshot
from .service import SharingService
from .store import Store


class GuardedMCPServer(MCPServer):
    """Reject extra raw arguments before SDK argument-model coercion/omission."""
    async def call_tool(self, name, arguments, context=None):
        allowed = {"list_documents": set(), "share_document": {"document_id", "recipient"}}
        if name in allowed and set(arguments) - allowed[name]:
            raise ToolError("Unexpected tool arguments; metadata and approvals are server-owned.")
        return await super().call_tool(name, arguments, context)

    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            tool.input_schema["additionalProperties"] = False
        return tools


def create_server(service: SharingService, session=None) -> MCPServer:
    server = GuardedMCPServer("VeriAgent")

    @server.tool()
    def list_documents() -> list[DocumentSummary]:
        """List synthetic documents and their owner-controlled classifications."""
        if session:
            session.received("list_documents")
        return service.list_documents()

    @server.tool(structured_output=True)
    def share_document(document_id: Annotated[str, Field(strict=True, min_length=1, max_length=200)],
                       recipient: Annotated[str, Field(strict=True, max_length=1000)]) -> dict[str, Any]:
        """Request simulated sharing. Trusted metadata and reviewed policies decide execution."""
        if session:
            session.received("share_document")
        return service.share_document(document_id, recipient).model_dump(mode="json", exclude_none=True)

    return server


def main():
    import argparse
    from .state import resolve_home
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path)
    args = parser.parse_args()
    home = resolve_home(args.home)
    store = Store(home / "demo.sqlite3")
    store.initialize()
    from .connection import Session
    server = create_server(SharingService(store, home=home), Session(home))
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
