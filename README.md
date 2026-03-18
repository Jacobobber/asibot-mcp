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
- View file details (size, author, sharing status)
- Browse file version history

**Outlook**
- Search your email
- Read full email threads
- Send emails
- Browse recent messages by folder
- List all mail folders
- View email attachments

**Teams**
- List your teams and channels
- Read channel conversations
- Search across all messages
- View recent chats
- List team members
- Send channel messages

**Calendar**
- View upcoming meetings and events
- Create new calendar events with attendees

---

### Google Workspace

Sign in with your Google account to connect Drive and Calendar.

**Google Drive**
- Search across all your files
- Browse folder contents
- Read documents (Docs, Sheets, text, PDF)
- View file details (owners, sharing, size)

**Google Calendar**
- View upcoming events
- Get full event details with attendees
- Create new events

---

### GitHub

Sign in with your GitHub account.

- Search repositories and code
- List repos in your organization
- Browse issues and pull requests
- Read issue details and comments
- Create new issues
- View pull request details and diff stats
- Browse commit history
- List releases
- List branches
- Check CI/CD workflow run status

---

### Jira

- Search issues with JQL or plain text
- View issue details with full comment history
- List projects
- See your assigned issues
- Create new issues
- Add comments to existing issues
- List sprints on a board
- View available status transitions
- Move issues between statuses

---

### Confluence

- Search pages across all spaces
- Read full page content
- List all spaces
- List pages within a space
- View page version history
- List page attachments
- Create new pages

---

### Salesforce

- Search records across objects
- Run SOQL queries
- Get full record details by type and ID
- Describe object fields and picklist values
- Create new records
- Update existing records

---

### HubSpot

- Search contacts, deals, and companies
- View detailed contact, deal, and company profiles
- View deal pipelines and stages
- View activity timeline for any record
- Create new contacts
- Create new deals

---

### Zendesk

- Search support tickets
- Read ticket details with full comment threads
- Search Help Center articles
- Look up users and agents
- View user profiles
- Create new tickets
- Add comments to tickets

---

### Notion

- Search across pages and databases
- Read full page content
- List all databases
- Query and filter database entries
- Create new pages
- Update page properties
- Add entries to databases

---

### Figma

- List team projects and files
- View file structure and metadata
- Read file comments
- Browse file version history
- List components in a file
- List design styles and tokens

---

### Smartsheet

- List all accessible sheets
- Read sheet data with rows and columns
- Search across sheets
- View individual row details
- List column definitions
- Add new rows

---

### Zoom

- List upcoming meetings
- View meeting details
- Browse cloud recordings
- View meeting participant lists
- List past meetings
- Get recording transcripts

---

### Adobe Sign

- List agreements
- View agreement details and status
- Get signing URLs
- View audit trail and signing events
- List document templates
- Get filled form field data

---

### SAP

- List and search sales orders
- View order details
- View order line items
- Look up customer/business partner details
- View delivery schedule lines

---

### SAP Concur

- List expense reports
- View report details with line items
- List individual expense entries
- View expense entry details
- List reports pending approval

---

### Citrix ShareFile

- Browse files and folders
- Search for documents
- View file and folder details
- Download text file content
- List shared links

---

### LinkSquares

- List contracts
- Search across contract data
- View full contract details
- View AI-extracted smart values
- List contract amendments

---

### Paylocity

- List employees
- View employee details
- Search employees by name
- View pay statements
- List departments

---

### RingCentral

- View call log
- Browse recent messages
- Get call recording details
- Check presence and availability status
- List company extensions and directory
- View voicemail messages

---

### Roboflow

- List workspace projects
- View project details
- List dataset versions
- View version details and preprocessing
- View model performance metrics

---

### Zapier

- List your configured Zapier actions
- Run any Zapier action using natural language
- Preview an action before running it (dry run)
- View action details and parameters

---

## Managing Connections

Once Asibot is installed, you can manage everything through conversation:

- **"Connect me to Jira"** — Claude will walk you through authentication
- **"What services am I connected to?"** — see all your active connections
- **"Disconnect Salesforce"** — remove stored credentials for a service
- **"Set GitHub to read-only"** — control access levels per service
