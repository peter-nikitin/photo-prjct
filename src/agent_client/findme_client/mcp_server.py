import json
from uuid import UUID

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from findme_client.api import ClientError, Json
from findme_client.config import api_for


def run_mcp(profile: str) -> None:
    server = MCPServer("FindMe Photo")
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=True)

    def read(path: str, page: int | None = None) -> CallToolResult:
        try:
            with api_for(profile) as api:
                return tool_result(api.request(path, page=page))
        except ClientError as exc:
            return tool_result(exc.payload(), failed=True)
        except (ValueError, OSError):
            return failed_result("profile_error", "Cannot read the local profile.")

    @server.tool(annotations=read_only)
    def list_events(page: int = 1) -> CallToolResult:
        """List existing events on a numbered page."""
        return read("events/", page)

    @server.tool(annotations=read_only)
    def get_event(event_id: int) -> CallToolResult:
        """Read the name, dates and publication state of an event."""
        return read(f"events/{event_id}/")

    @server.tool(annotations=read_only)
    def list_folders(event_id: int, page: int = 1) -> CallToolResult:
        """List destination folders for an event."""
        return read(f"events/{event_id}/folders/", page)

    @server.tool(annotations=read_only)
    def list_batches(page: int = 1) -> CallToolResult:
        """List upload batches owned by the authenticated account."""
        return read("batches/", page)

    @server.tool(annotations=read_only)
    def get_batch(batch_id: str) -> CallToolResult:
        """Read original acceptance counts and downstream processing state for an owned batch."""
        try:
            batch = str(UUID(batch_id))
        except ValueError:
            return failed_result("invalid_batch", "Enter the batch UUID.")
        return read(f"batches/{batch}/")

    @server.tool(annotations=read_only)
    def list_items(batch_id: str, page: int = 1) -> CallToolResult:
        """List individual upload items and their original acceptance state."""
        try:
            batch = str(UUID(batch_id))
        except ValueError:
            return failed_result("invalid_batch", "Enter the batch UUID.")
        return read(f"batches/{batch}/items/", page)

    server.run(transport="stdio")


def tool_result(payload: Json, *, failed: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
        is_error=failed,
    )


def failed_result(code: str, message: str) -> CallToolResult:
    return tool_result(ClientError(code, message).payload(), failed=True)
