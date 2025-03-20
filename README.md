# Logseq MCP Server
A Model Context Protocol server that provides direct integration with Logseq's knowledge base. This server enables LLMs to interact with Logseq graphs, create pages, manage blocks, and organize information programmatically.

## Usage with Claude Desktop
```json
{
  "mcpServers": {
    "logseq": {
      "command": "uvx",
      "args": ["mcp-server-logseq"],
      "env": {
        "LOGSEQ_API_TOKEN": "<YOUR_KEY>",
        "LOGSEQ_API_URL": "http://127.0.0.1:12315"
      }
    }
  }
}
```

## Available Tools

### Block Operations
- **logseq_insert_block** - Create new blocks in Logseq
  **Parameters**:
  - `parent_block` (string): Parent block UUID or page name
  - `content` (string, required): Block content
  - `is_page_block` (boolean): Create as page-level block
  - `before` (boolean): Insert before parent block
  - `custom_uuid` (string): Custom UUIDv4 for block

- **logseq_edit_block** - Enter block editing mode
  **Parameters**:
  - `src_block` (string, required): Block UUID
  - `pos` (number): Cursor position

- **logseq_exit_editing_mode** - Exit editing mode
  **Parameters**:
  - `select_block` (boolean): Keep block selected

### Page Operations
- **logseq_create_page** - Create new pages
  **Parameters**:
  - `page_name` (string, required): Page name
  - `properties` (object): Page properties
  - `journal` (boolean): Create as journal page
  - `format` (string): Page format (markdown/org)

- **logseq_get_page** - Get page details
  **Parameters**:
  - `src_page` (string, required): Page identifier
  - `include_children` (boolean): Include child blocks

- **logseq_get_all_pages** - List all pages
  **Parameters**:
  - `repo` (string): Repository name

### Content Retrieval
- **logseq_get_current_page** - Get active page/block
  **Parameters**: None

- **logseq_get_current_blocks_tree** - Current page's block hierarchy
  **Parameters**: None

- **logseq_get_editing_block_content** - Get content of active block
  **Parameters**: None

- **logseq_get_page_blocks_tree** - Get page's block structure
  **Parameters**:
  - `src_page` (string, required): Page identifier

## Prompts

### logseq_insert_block
Create a new block in Logseq
**Arguments:**
- `parent_block`: Parent block reference (page name or UUID)
- `content`: Block content
- `is_page_block`: Set true for page-level blocks

### logseq_create_page
Create a new Logseq page
**Arguments:**
- `page_name`: Name of the page
- `properties`: Page properties as JSON
- `journal`: Set true for journal pages

## Installation

### Using pip
```bash
pip install mcp-server-logseq
```
### From source
```bash
git clone https://github.com/dailydaniel/logseq-mcp.git
cd logseq-mcp
cp .env.example .env
uv sync
```
Run the server:
```bash
python -m mcp_server_logseq
```
## Configuration
### API Key
1. Generate API token in Logseq: API → Authorization tokens
2. Set environment variable:
```bash
export LOGSEQ_API_TOKEN=your_token_here
```
Or pass via command line:
```bash
python -m mcp_server_logseq --api-key=your_token_here
```
### Graph Configuration
Default URL: http://localhost:12315
To customize:
```bash
python -m mcp_server_logseq --url=http://your-logseq-instance:port
```
## Examples
## Create meeting notes page
```plaintext
Create new page "Team Meeting 2024-03-15" with properties:
- Tags: #meeting #engineering
- Participants: Alice, Bob, Charlie
- Status: pending
```
### Add task block to existing page
```plaintext
Add task to [[Project Roadmap]]:
- [ ] Finalize API documentation
- Due: 2024-03-20
- Priority: high
```
### Create journal entry with first block
```plaintext
Create journal entry for today with initial content:
- Morning standup completed
- Started work on new authentication system
```
## Debugging
```bash
npx @modelcontextprotocol/inspector uv --directory . run mcp-server-logseq
```
## Contributing
We welcome contributions to enhance Logseq integration:
- Add new API endpoints (page linking, query support)
- Improve block manipulation capabilities
- Add template support
- Enhance error handling
