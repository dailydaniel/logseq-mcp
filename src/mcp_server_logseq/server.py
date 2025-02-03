from typing import Annotated, Optional
from mcp.server import Server
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData
from mcp.server.stdio import stdio_server
from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    TextContent,
    Tool,
    INVALID_PARAMS,
    INTERNAL_ERROR,
)
from pydantic import BaseModel, Field, field_validator, ConfigDict
import requests
import json


class LogseqBaseModel(BaseModel):
    """Base model with Pydantic configuration"""
    model_config = ConfigDict(extra='forbid', validate_assignment=True)

class InsertBlockParams(LogseqBaseModel):
    """Parameters for inserting a new block in Logseq."""
    parent_block: Annotated[
        Optional[str],
        Field(default=None, description="UUID or content of parent block")
    ]
    content: Annotated[
        str,
        Field(description="Content of the new block")
    ]
    is_page_block: Annotated[
        Optional[bool],
        Field(default=False, description="Page-level block flag")
    ]
    before: Annotated[
        Optional[bool],
        Field(default=False, description="Insert before parent")
    ]
    custom_uuid: Annotated[
        Optional[str],
        Field(default=None, description="Custom UUID for block")
    ]

    @field_validator('parent_block', 'custom_uuid', mode='before')
    @classmethod
    def validate_block_references(cls, value):
        """Validate block/page references"""
        if value and isinstance(value, str):
            if value.startswith('((') and value.endswith('))'):
                return value.strip('()')
        return value

class CreatePageParams(LogseqBaseModel):
    """Parameters for creating a new page in Logseq."""
    page_name: Annotated[
        str,
        Field(description="Name of the page to create")
    ]
    properties: Annotated[
        Optional[dict],
        Field(default=None, description="Page properties")
    ]
    journal: Annotated[
        Optional[bool],
        Field(default=False, description="Journal page flag")
    ]
    format: Annotated[
        Optional[str],
        Field(default="markdown", description="Page format")
    ]
    create_first_block: Annotated[
        Optional[bool],
        Field(default=True, description="Create initial block")
    ]

    @field_validator('properties', mode='before')
    @classmethod
    def parse_properties(cls, value):
        """Parse properties from JSON string if needed"""
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                raise ValueError("Invalid JSON format for properties")
        return value or {}


async def serve(
    api_key: str,
    logseq_url: str = "http://localhost:12315"
) -> None:
    """Run the Logseq MCP server.

    Args:
        api_key: Logseq API token for authentication
        logseq_url: Base URL of Logseq graph (default: http://localhost:12315)
    """
    server = Server("mcp-sever-logseq")

    def make_request(method: str, args: list) -> dict:
        """Make authenticated request to Logseq API."""
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
        payload = {"method": method, "args": args}

        try:
            response = requests.post(
                f"{logseq_url}/api",
                headers=headers,
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401:
                raise McpError(ErrorData(INTERNAL_ERROR, "Invalid API token"))
            raise McpError(ErrorData(INTERNAL_ERROR, f"API request failed: {str(e)}"))
        except requests.exceptions.RequestException as e:
            raise McpError(ErrorData(INTERNAL_ERROR, f"Network error: {str(e)}"))

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="logseq_insert_block",
                description="""Insert a new block into Logseq. Can create:
                - Page-level blocks (use is_page_block=true with page name as parent_block)
                - Nested blocks under existing blocks
                - Blocks with custom UUIDs for precise reference
                Supports before/after positioning and property management.""",
                inputSchema=InsertBlockParams.model_json_schema(),
            ),
            Tool(
                name="logseq_create_page",
                description="""Create a new page in Logseq with optional properties.
                Features:
                - Journal page creation with date formatting
                - Custom page properties (tags, status, etc.)
                - Format selection (Markdown/Org-mode)
                - Automatic first block creation
                Perfect for template-based page creation and knowledge management.""",
                inputSchema=CreatePageParams.model_json_schema(),
            )
        ]

    @server.list_prompts()
    async def list_prompts() -> list[Prompt]:
        return [
            Prompt(
                name="logseq_insert_block",
                description="Create a new block in Logseq",
                arguments=[
                    PromptArgument(
                        name="parent_block",
                        description="Parent block UUID or page name (for page blocks)",
                        required=False,
                    ),
                    PromptArgument(
                        name="content",
                        description="Block content in Markdown/Org syntax",
                        required=True,
                    ),
                    PromptArgument(
                        name="is_page_block",
                        description="Set true for page-level blocks",
                        required=False,
                    ),
                ],
            ),
            Prompt(
                name="logseq_create_page",
                description="Create a new Logseq page",
                arguments=[
                    PromptArgument(
                        name="page_name",
                        description="Name of the page to create",
                        required=True,
                    ),
                    PromptArgument(
                        name="properties",
                        description="Optional page properties as JSON",
                        required=False,
                    ),
                    PromptArgument(
                        name="journal",
                        description="Set true for journal pages",
                        required=False,
                    ),
                ],
            ),
        ]

    def format_block_result(result: dict) -> str:
        """Format block creation result into readable text."""
        return (
            f"Created block in {result.get('page', {}).get('name', 'unknown page')}\n"
            f"UUID: {result.get('uuid')}\n"
            f"Content: {result.get('content')}\n"
            f"Parent: {result.get('parent', {}).get('uuid') or 'None'}"
        )

    def format_page_result(result: dict) -> str:
        """Format page creation result into readable text."""
        return (
            f"Created page: {result.get('name')}\n"
            f"UUID: {result.get('uuid')}\n"
            f"Journal: {result.get('journal', False)}\n"
            f"Blocks: {len(result.get('blocks', []))}"
        )

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            if name == "logseq_insert_block":
                args = InsertBlockParams(**arguments)
                result = make_request(
                    "logseq.Editor.insertBlock",
                    [
                        args.parent_block,
                        args.content,
                        {
                            "isPageBlock": args.is_page_block,
                            "before": args.before,
                            "customUUID": args.custom_uuid
                        }
                    ]
                )
                return [TextContent(
                    type="text",
                    text=format_block_result(result)
                )]

            elif name == "logseq_create_page":
                args = CreatePageParams(**arguments)
                result = make_request(
                    "logseq.Editor.createPage",
                    [
                        args.page_name,
                        args.properties or {},
                        {
                            "journal": args.journal,
                            "format": args.format,
                            "createFirstBlock": args.create_first_block
                        }
                    ]
                )
                return [TextContent(
                    type="text",
                    text=format_page_result(result)
                )]

            else:
                raise McpError(ErrorData(INVALID_PARAMS, f"Unknown tool: {name}"))

        except ValueError as e:
            raise McpError(ErrorData(INVALID_PARAMS, str(e)))

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict | None) -> GetPromptResult:
        if not arguments:
            raise McpError(ErrorData(INVALID_PARAMS, "Missing arguments"))

        try:
            if name == "logseq_insert_block":
                if "content" not in arguments:
                    raise ValueError("Content is required for block creation")

                result = make_request(
                    "logseq.Editor.insertBlock",
                    [
                        arguments.get("parent_block"),
                        arguments["content"],
                        {"isPageBlock": arguments.get("is_page_block", False)}
                    ]
                )
                return GetPromptResult(
                    description=f"Created block: {arguments['content']}",
                    messages=[
                        PromptMessage(
                            role="user",
                            content=TextContent(
                                type="text",
                                text=format_block_result(result)
                            )
                        )
                    ]
                )

            elif name == "logseq_create_page":
                if "page_name" not in arguments:
                    raise ValueError("Page name is required")

                result = make_request(
                    "logseq.Editor.createPage",
                    [
                        arguments["page_name"],
                        json.loads(arguments.get("properties", "{}")),
                        {"journal": arguments.get("journal", False)}
                    ]
                )
                return GetPromptResult(
                    description=f"Created page: {arguments['page_name']}",
                    messages=[
                        PromptMessage(
                            role="user",
                            content=TextContent(
                                type="text",
                                text=format_page_result(result)
                            )
                        )
                    ]
                )

            else:
                raise McpError(ErrorData(INVALID_PARAMS, f"Unknown prompt: {name}"))

        except Exception as e:
            return GetPromptResult(
                description=f"Operation failed: {str(e)}",
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(type="text", text=str(e)),
                    )
                ],
            )

    options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options, raise_exceptions=True)

if __name__ == "__main__":
    import asyncio
    import os
    from dotenv import load_dotenv

    load_dotenv()

    api_key = os.getenv("LOGSEQ_API_TOKEN")
    if not api_key:
        raise ValueError("LOGSEQ_API_TOKEN environment variable is required")

    url = os.getenv("LOGSEQ_API_URL")
    if not url:
        url = "http://localhost:12315"

    asyncio.run(serve(api_key, url))
