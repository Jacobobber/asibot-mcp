# Asibot

Connect Claude to the tools your team already uses. Asibot is an MCP server that gives Claude access to 26 SaaS platforms — search files, read emails, query databases, create issues, and more, all from a single conversation.

## Install

Choose the setup that matches how you use Claude.

### Claude Desktop (Pro, Max, Team)

Add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "asibot": {
      "command": "asibot-stdio"
    }
  }
}
```

Then restart Claude Desktop and say:

> **Set up Asibot and connect me to [service name].**

Claude will walk you through the rest.

### Claude Code (CLI)

Run:

```
claude mcp add asibot asibot-stdio
```

Then ask Claude:

> **Set up Asibot and connect me to [service name].**

### Remote Server (Enterprise)

If your admin has deployed Asibot as a shared server, add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "asibot": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://your-server.com:8080/mcp",
        "--header",
        "Authorization:Bearer YOUR_API_KEY"
      ]
    }
  }
}
```

Ask your admin for the server URL and API key, or say:

> **Set up my Asibot account.**

Claude will guide you through sign-in.

---

## Connectors

### Microsoft 365

Sign in once with your Microsoft account to unlock SharePoint, Outlook, Teams, and Calendar.

**SharePoint**
- Search files and documents across your sites
- Browse folder contents
- Read files (Word, PDF, Excel, CSV, text, Markdown)
- List and search SharePoint sites

**Outlook**
- Search your email
- Read full email threads
- Send emails
- Browse recent messages by folder

**Teams**
- List your teams and channels
- Read channel conversations
- Search across all messages
- View recent chats

**Calendar**
- View upcoming meetings and events

---

### Google Workspace

Sign in with your Google account to connect Drive and Calendar.

**Google Drive**
- Search across all your files
- Browse folder contents
- Read documents (Docs, Sheets, text, PDF)

**Google Calendar**
- View upcoming events

---

### GitHub

Sign in with your GitHub account.

- Search repositories and code
- List repos in your organization
- Browse issues and pull requests
- Read issue details and comments
- Create new issues

---

### Jira

- Search issues with JQL or plain text
- View issue details with full comment history
- List projects
- See your assigned issues
- Create new issues
- Add comments to existing issues

---

### Confluence

- Search pages across all spaces
- Read full page content
- List all spaces

---

### Salesforce

- Search records across objects
- Run SOQL queries
- Get full record details by type and ID

---

### HubSpot

- Search contacts and deals
- View detailed contact profiles
- View detailed deal records

---

### Zendesk

- Search support tickets
- Read ticket details with full comment threads
- Search Help Center articles

---

### Notion

- Search across pages and databases
- Read full page content
- List all databases
- Query and filter database entries

---

### Figma

- List team projects and files
- View file structure and metadata
- Read file comments

---

### Smartsheet

- List all accessible sheets
- Read sheet data with rows and columns
- Search across sheets

---

### Zoom

- List upcoming meetings
- View meeting details
- Browse cloud recordings

---

### Adobe Sign

- List agreements
- View agreement details and status

---

### SAP

- List and search sales orders
- View order details

---

### SAP Concur

- List expense reports
- View report details with line items

---

### Citrix ShareFile

- Browse files and folders
- Search for documents

---

### LinkSquares

- List contracts
- Search across contract data

---

### Paylocity

- List employees
- View employee details

---

### RingCentral

- View call log
- Browse recent messages

---

### Roboflow

- List workspace projects
- View project details

---

### Zapier

- List your configured Zapier actions
- Run any Zapier action using natural language

---

## Managing Connections

Once Asibot is installed, you can manage everything through conversation:

- **"Connect me to Jira"** — Claude will walk you through authentication
- **"What services am I connected to?"** — see all your active connections
- **"Disconnect Salesforce"** — remove stored credentials for a service
- **"Set GitHub to read-only"** — control access levels per service
