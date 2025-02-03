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


class GetCurrentPageParams(LogseqBaseModel):
    """Parameters for getting current page (no arguments needed)"""


class GetPageParams(LogseqBaseModel):
    """Parameters for retrieving a specific page"""
    src_page: Annotated[
        str | int,
        Field(
            description="Page identifier (name, UUID or database ID)",
            examples=["[[Journal/2024-03-15]]", 12345]
        )
    ]
    include_children: Annotated[
        Optional[bool],
        Field(
            default=False,
            description="Include child blocks in response"
        )
    ]


class GetAllPagesParams(LogseqBaseModel):
    """Parameters for listing all pages"""
    repo: Annotated[
        Optional[str],
        Field(
            default=None,
            description="Repository name (default: current graph)"
        )
    ]


class EditBlockParams(LogseqBaseModel):
    src_block: Annotated[
        str,
        Field(description="Block UUID or reference", examples=["6485a-9de3...", "[[Page/Block]]"])
    ]
    pos: Annotated[
        int,
        Field(
            default=0,
            description="Cursor position in block content",
            ge=0,
            le=10000
        )
    ]


class ExitEditingModeParams(LogseqBaseModel):
    select_block: Annotated[
        bool,
        Field(
            default=False,
            description="Keep block selected after exiting edit mode"
        )
    ]


class GetPageBlocksTreeParams(LogseqBaseModel):
    src_page: Annotated[
        str,
        Field(description="Page name or UUID", examples=["[[Journal]]", "6485a-9de3..."])
    ]


class EmptyParams(LogseqBaseModel):
    pass

class GetEditingBlockContentParams(LogseqBaseModel):
    pass

class GetCurrentBlocksTreeParams(LogseqBaseModel):
    pass


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
            ),
            Tool(
                name="logseq_get_current_page",
                description="Retrieves the currently active page or block in the user's workspace",
                inputSchema=GetCurrentPageParams.model_json_schema(),
            ),
            Tool(
                name="logseq_get_page",
                description="Retrieve detailed information about a specific page including metadata and content",
                inputSchema=GetPageParams.model_json_schema(),
            ),
            Tool(
                name="logseq_get_all_pages",
                description="List all pages in the graph with basic metadata",
                inputSchema=GetAllPagesParams.model_json_schema(),
            ),
            Tool(
                name="logseq_edit_block",
                description="Enter editing mode for a specific block",
                inputSchema=EditBlockParams.model_json_schema(),
            ),
            Tool(
                name="logseq_exit_editing_mode",
                description="Exit current editing mode",
                inputSchema=ExitEditingModeParams.model_json_schema(),
            ),
            Tool(
                name="logseq_get_current_page_content",
                description="Get hierarchical block structure of current page",
                inputSchema=GetCurrentBlocksTreeParams.model_json_schema()  # No parameters
            ),
            Tool(
                name="logseq_get_editing_block_content",
                description="Get content of currently edited block",
                inputSchema=GetEditingBlockContentParams.model_json_schema()  # No parameters
            ),
            Tool(
                name="logseq_get_page_content",
                description="Get block hierarchy for specific page",
                inputSchema=GetPageBlocksTreeParams.model_json_schema(),
            ),
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
            Prompt(
                name="logseq_get_current_page",
                description="Get the currently active page or block",
                arguments=[]
            ),
            Prompt(
                name="logseq_get_page",
                description="Retrieve information about a specific page",
                arguments=[
                    PromptArgument(
                        name="src_page",
                        description="Page name, UUID or database ID",
                        required=True
                    )
                ]
            ),
            Prompt(
                name="logseq_get_all_pages",
                description="List all pages in the graph",
                arguments=[
                    PromptArgument(
                        name="repo",
                        description="Repository name (optional)",
                        required=False
                    )
                ]
            ),
            Prompt(
                name="logseq_edit_block",
                description="Edit specific block content",
                arguments=[
                    PromptArgument(
                        name="src_block",
                        description="Block identifier",
                        required=True
                    )
                ]
            ),
            Prompt(
                name="logseq_exit_editing_mode",
                description="Exit block editing mode",
                arguments=[
                    PromptArgument(
                        name="select_block",
                        description="Keep block selected",
                        required=False
                    )
                ]
            ),
            Prompt(
                name="logseq_get_current_page_content",
                description="Get current page's content by each block",
                arguments=[]
            ),
            Prompt(
                name="logseq_get_editing_block_content",
                description="Get content of active editing block",
                arguments=[]
            ),
            Prompt(
                name="logseq_get_page_content",
                description="Get block page content by each block",
                arguments=[
                    PromptArgument(
                        name="src_page",
                        description="Page identifier",
                        required=True
                    )
                ]
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

    def format_page_detail(page: dict) -> str:
        """Format single page details"""
        return (
            f"Page: {page.get('name', 'Unnamed')}\n"
            f"UUID: {page.get('uuid')}\n"
            f"Created: {page.get('createdAt', 0)}\n"
            f"Updated: {page.get('updatedAt', 0)}\n"
            f"Blocks: {len(page.get('blocks', []))}"
        )

    def format_pages_list(pages: list) -> str:
        """Format list of pages"""
        return "\n".join(
            f"{p['name']} (UUID: {p['uuid']})"
            for p in sorted(pages, key=lambda x: x.get('name', ''))
        )

    def _format_current_page(result: dict) -> str:
        """Special formatting for current page/block context"""
        entity_type = "Page" if 'name' in result else "Block"
        return (
            f"Current {entity_type}: {result.get('name', result.get('content', 'Untitled'))}\n"
            f"UUID: {result.get('uuid')}\n"
            f"Last updated: {result.get('updatedAt', 'N/A')}"
        )

    def format_blocks_tree(blocks: list) -> str:
        """Format hierarchical block structure"""
        def print_tree(block, level=0):
            output = []
            prefix = "  " * level + "- "
            output.append(f"{prefix}{block.get('content', '')}")
            for child in block.get('children', []):
                output.extend(print_tree(child, level + 1))
            return output

        return "\n".join(
            line for block in blocks
            for line in print_tree(block)
        )

    def _format_no_arg_result(method_name: str, result: dict) -> str:
        """Format results for methods without arguments"""
        formatters = {
            'logseq_get_current_page_content': lambda r: format_blocks_tree(r),
            'logseq_get_editing_block_content': lambda r: f"Current content:\n{r}",
            'logseq_get_current_page': _format_current_page
        }
        return formatters[method_name](result)

    def format_no_arg_result(name: str, result) -> str:
        """Format results for methods without arguments"""
        formatters = {
            'logseq_get_current_page': lambda r: (
                f"Current: {r.get('name', r.get('content', 'Untitled'))}\n"
                f"UUID: {r.get('uuid')}\n"
                f"Last updated: {r.get('updatedAt', 'N/A')}"
            ),
            'logseq_get_current_page_content': lambda r: format_blocks_tree(r),
            'logseq_get_editing_block_content': lambda r: f"Current content:\n{r}",
            'logseq_get_all_pages': lambda r: "\n".join(
                f"{p['name']} ({p['uuid']})" for p in sorted(r, key=lambda x: x['name'])
            )
        }
        return formatters[name](result)

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

            elif name == "logseq_get_current_page":
                args = GetCurrentPageParams(**arguments)
                result = make_request(
                    "logseq.Editor.getCurrentPage",
                    []
                )
                return [TextContent(
                    type="text",
                    text=format_page_result(result)
                )]

            elif name == "logseq_get_page":
                args = GetPageParams(**arguments)
                result = make_request(
                    "logseq.Editor.getPage",
                    [
                        args.src_page,
                        {"includeChildren": args.include_children}
                    ]
                )
                return [TextContent(
                    type="text",
                    text=format_page_detail(result)
                )]

            elif name == "logseq_get_all_pages":
                args = GetAllPagesParams(**arguments)
                result = make_request(
                    "logseq.Editor.getAllPages",
                    [args.repo] if args.repo else []
                )
                return [TextContent(
                    type="text",
                    text=format_pages_list(result)
                )]

            elif name == "logseq_edit_block":
                args = EditBlockParams(**arguments)
                result = make_request(
                    "logseq.Editor.editBlock",
                    [args.src_block, {"pos": args.pos}]
                )
                return [TextContent(
                    type="text",
                    text=f"Editing block {args.src_block} at position {args.pos}"
                )]

            elif name == "logseq_exit_editing_mode":
                args = ExitEditingModeParams(**arguments)
                make_request(
                    "logseq.Editor.exitEditingMode",
                    [args.select_block]
                )
                return [TextContent(
                    type="text",
                    text="Exited editing mode" +
                         (" with block selected" if args.select_block else "")
                )]

            elif name == "logseq_get_current_page_content":
                result = make_request("logseq.Editor.getCurrentPageBlocksTree", [])
                return [TextContent(
                    type="text",
                    text=format_blocks_tree(result)
                )]

            elif name == "logseq_get_editing_block_content":
                result = make_request("logseq.Editor.getEditingBlockContent", [])
                return [TextContent(
                    type="text",
                    text=f"Current editing block content:\n{result}"
                )]

            elif name == "logseq_get_page_content":
                args = GetPageBlocksTreeParams(**arguments)
                result = make_request(
                    "logseq.Editor.getPageBlocksTree",
                    [args.src_page]
                )
                return [TextContent(
                    type="text",
                    text=format_blocks_tree(result)
                )]

            else:
                raise McpError(ErrorData(INVALID_PARAMS, f"Unknown tool: {name}"))

        except ValueError as e:
            raise McpError(ErrorData(INVALID_PARAMS, str(e)))

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict | None) -> GetPromptResult:
        try:
            # Handle methods that don't require arguments
            no_arg_methods = {
                'logseq_get_current_page',
                'logseq_get_current_page_content',
                'logseq_get_editing_block_content',
                'logseq_get_all_pages'
            }

            # Normalize arguments
            if arguments is None:
                arguments = {}

            # Automatic handling for no-argument methods
            if name in no_arg_methods and not arguments:
                api_method = name.split('_', 1)[1].replace('_', '.')
                result = make_request(f"logseq.Editor.{api_method}", [])
                return GetPromptResult(
                    description=f"Current {name.split('_')[-1].replace('_', ' ')}",
                    messages=[
                        PromptMessage(
                            role="user",
                            content=TextContent(
                                type="text",
                                text=format_no_arg_result(name, result)
                            )
                        )
                    ]
                )

            # Handle methods with arguments
            if name == "logseq_insert_block":
                required_args = ["content"]
                if not all(k in arguments for k in required_args):
                    raise ValueError(f"Missing required arguments: {required_args}")

                result = make_request(
                    "logseq.Editor.insertBlock",
                    [
                        arguments.get("parent_block"),
                        arguments["content"],
                        {
                            "isPageBlock": arguments.get("is_page_block", False),
                            "before": arguments.get("before", False),
                            "customUUID": arguments.get("custom_uuid")
                        }
                    ]
                )
                return GetPromptResult(
                    description=f"Created block: {arguments['content'][:50]}...",
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
                    raise ValueError("page_name is required")

                result = make_request(
                    "logseq.Editor.createPage",
                    [
                        arguments["page_name"],
                        arguments.get("properties", {}),
                        {
                            "journal": arguments.get("journal", False),
                            "format": arguments.get("format", "markdown"),
                            "createFirstBlock": arguments.get("create_first_block", True),
                            "redirect": arguments.get("redirect", False)
                        }
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

            elif name == "logseq_get_page":
                if "src_page" not in arguments:
                    raise ValueError("src_page is required")

                result = make_request(
                    "logseq.Editor.getPage",
                    [
                        arguments["src_page"],
                        {"includeChildren": arguments.get("include_children", False)}
                    ]
                )
                return GetPromptResult(
                    description=f"Details for {arguments['src_page']}",
                    messages=[
                        PromptMessage(
                            role="user",
                            content=TextContent(
                                type="text",
                                text=format_page_detail(result)
                            )
                        )
                    ]
                )

            elif name == "logseq_edit_block":
                if "src_block" not in arguments:
                    raise ValueError("src_block is required")

                pos = arguments.get("pos", 0)
                make_request(
                    "logseq.Editor.editBlock",
                    [arguments["src_block"], {"pos": pos}]
                )
                return GetPromptResult(
                    description=f"Editing block {arguments['src_block']}",
                    messages=[
                        PromptMessage(
                            role="user",
                            content=TextContent(
                                type="text",
                                text=f"Editing mode activated at position {pos}"
                            )
                        )
                    ]
                )

            elif name == "logseq_exit_editing_mode":
                select_block = arguments.get("select_block", False)
                make_request("logseq.Editor.exitEditingMode", [select_block])
                return GetPromptResult(
                    description="Exited editing mode",
                    messages=[
                        PromptMessage(
                            role="user",
                            content=TextContent(
                                type="text",
                                text="Exited editing" +
                                     (" with block selected" if select_block else "")
                            )
                        )
                    ]
                )

            elif name == "logseq_get_page_content":
                if "src_page" not in arguments:
                    raise ValueError("src_page is required")

                result = make_request(
                    "logseq.Editor.getPageBlocksTree",
                    [arguments["src_page"]]
                )
                return GetPromptResult(
                    description=f"Block structure for {arguments['src_page']}",
                    messages=[
                        PromptMessage(
                            role="user",
                            content=TextContent(
                                type="text",
                                text=format_blocks_tree(result)
                            )
                        )
                    ]
                )

            elif name == "logseq_get_all_pages":
                repo = arguments.get("repo")
                result = make_request(
                    "logseq.Editor.getAllPages",
                    [repo] if repo else []
                )
                return GetPromptResult(
                    description=f"All pages in {repo or 'current graph'}",
                    messages=[
                        PromptMessage(
                            role="user",
                            content=TextContent(
                                type="text",
                                text=format_pages_list(result)
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
