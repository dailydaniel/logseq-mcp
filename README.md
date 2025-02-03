# Logseq MCP Server
A Model Context Protocol server that provides direct integration with Logseq's knowledge base. This server enables LLMs to interact with Logseq graphs, create pages, manage blocks, and organize information programmatically.

## Available Tools

### logseq_insert_block - Creates new blocks in Logseq
**Parameters:**
- `parent_block` (string, optional): UUID or content of parent block/page
- `content` (string, required): Block content in Markdown/Org format
- `is_page_block` (boolean, optional): Create as page-level block (default: false)
- `before` (boolean, optional): Insert before parent block (default: false)
- `custom_uuid` (string, optional): Custom UUIDv4 for the block

### logseq_create_page - Creates new pages with properties
**Parameters:**
- `page_name` (string, required): Name of the page to create
- `properties` (object, optional): Page properties as key-value pairs
- `journal` (boolean, optional): Create as journal page (default: false)
- `format` (string, optional): Page format - "markdown" or "org" (default: "markdown")
- `create_first_block` (boolean, optional): Create initial empty block (default: true)

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
todo: add to pypi
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
