> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Context templates

> Create reusable context templates to shape the format and contents of your Zep Context Block while keeping automatic relevance detection.

Context templates allow you to customize how context is formatted and returned when calling `thread.get_user_context()`. Templates are reusable configurations that you set once for your project and can use across all your threads. They let you specify what information goes into your context block and how much of it to include, while Zep handles the automatic relevance detection and retrieval.

## Why use context templates

Context templates let you define a custom context format once and reuse it across all threads and users in your project. They provide a balance between simplicity and control:

* **More control than**: Default Context Block (customize format/structure)
* **Less control than**: Advanced construction with graph search (cannot customize search query)
* **Best for**: When you need consistent custom formatting but want automatic relevance detection

See [Choosing a retrieval method](/retrieving-context#choosing-a-retrieval-method) for a comparison of all three methods.

## Define a template

Templates use variables to specify what data to include. Available variables:

* `%{edges}` - Graph edges ([facts](/facts) / relationships)
* `%{entities}` - Graph [entities](/entities) (nodes)
* `%{episodes}` - [Episode](/episodes) data
* `%{observations}` - [Observations](/observations) (cross-entity context derived from the graph)
* `%{thread_summaries}` - [Thread summaries](/thread-summaries) for the user's threads
* `%{user_summary}` - [User summary](/user-summary) information

Variables (except `user_summary`) accept optional parameters:

* `limit=N` - Limit number of results (max 1000)
* `types=[type1,type2]` - Filter by entity or edge types
* `include_attributes=true/false` - Include/exclude attributes

`%{observations}` and `%{thread_summaries}` only support `limit`.

`%{observations}` and `%{thread_summaries}` are each capped at 20 items per render. A template `limit=N` greater than 20 can only truncate further; it cannot raise the count above this ceiling.

Example template definition:

```
# CUSTOMER PROFILE
# A summary of who this user is and their background.
%{user_summary}

# FACTS
# Key facts and relationships extracted from conversations.
%{edges limit=10}

# KEY ENTITIES
# Important people, organizations, and concepts related to this user.
%{entities limit=5 types=[person,organization]}
```

## Create a template

Create a new template with a unique template ID and template content. Templates are validated when created—Zep checks for valid variable names, proper bracket balancing, valid parameter syntax, and limit values within range.

```python Python
from zep_cloud import Zep

client = Zep(api_key="YOUR_API_KEY")

client.context.create_context_template(
    template_id="customer-support",
    template="""# CUSTOMER PROFILE
# A summary of who this user is and their background.
%{user_summary}

# FACTS
# Key facts and relationships extracted from conversations.
%{edges limit=10}

# KEY ENTITIES
# Important people, organizations, and concepts related to this user.
%{entities limit=5}"""
)
```

```typescript TypeScript
import { Zep } from "@getzep/zep-cloud";

const client = new Zep({ apiKey: "YOUR_API_KEY" });

await client.context.createContextTemplate({
    templateId: "customer-support",
    template: `# CUSTOMER PROFILE
# A summary of who this user is and their background.
%{user_summary}

# FACTS
# Key facts and relationships extracted from conversations.
%{edges limit=10}

# KEY ENTITIES
# Important people, organizations, and concepts related to this user.
%{entities limit=5}`
});
```

```go Go
import (
    "context"
    zep "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/context"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey("YOUR_API_KEY"),
)

_, err := client.CreateContextTemplate(
    context.TODO(),
    &zep.CreateContextTemplateRequest{
        TemplateID: "customer-support",
        Template: `# CUSTOMER PROFILE
# A summary of who this user is and their background.
%{user_summary}

# FACTS
# Key facts and relationships extracted from conversations.
%{edges limit=10}

# KEY ENTITIES
# Important people, organizations, and concepts related to this user.
%{entities limit=5}`,
    },
)
```

## Use a template

Pass the `template_id` parameter when retrieving context:

```python Python
from zep_cloud import Zep

client = Zep(api_key="YOUR_API_KEY")

user_context = client.thread.get_user_context(
    thread_id="thread_id",
    template_id="customer-support"
)
context_block = user_context.context
```

```typescript TypeScript
import { Zep } from "@getzep/zep-cloud";

const client = new Zep({ apiKey: "YOUR_API_KEY" });

const userContext = await client.thread.getUserContext("thread_id", {
    templateId: "customer-support"
});
const contextBlock = userContext.context;
```

```go Go
import (
    "context"
    zep "github.com/getzep/zep-go/v3"
    threadclient "github.com/getzep/zep-go/v3/thread/client"
    "github.com/getzep/zep-go/v3/option"
)

client := threadclient.NewClient(
    option.WithAPIKey("YOUR_API_KEY"),
)

templateID := "customer-support"
userContext, err := client.GetUserContext(
    context.TODO(),
    "thread_id",
    &zep.ThreadGetUserContextRequest{
        TemplateID: &templateID,
    },
)
contextBlock := userContext.Context
```

## Resulting context block

When you use the template above, Zep returns a formatted context block like this:

```text
# CUSTOMER PROFILE
# A summary of who this user is and their background.
Emily Johnson is a long-time enterprise customer who has been using the platform since March 2024. She is the technical lead for TechCorp Solutions' API integration team and prefers asynchronous communication methods. Emily has shown strong interest in advanced features like SSO integration and webhook capabilities.

# FACTS
# Key facts and relationships extracted from conversations.
- (2024-11-15 14:23:00 - present) [CONTACTED_SUPPORT_ABOUT] <Emily contacted support regarding API rate limit concerns for high-volume usage>
- (2024-11-10 09:15:00 - present) [UPGRADED_TO] <Emily upgraded her organization's subscription from Pro to Enterprise plan>
- (2024-11-08 16:45:00 - present) [REQUESTED_DOCUMENTATION_FOR] <Emily requested comprehensive documentation for webhook integration patterns>
- (2024-11-05 11:00:00 - present) [ATTENDED] <Emily attended the product webinar focused on advanced enterprise features>
- (2024-11-01 13:30:00 - present) [INQUIRED_ABOUT] <Emily inquired about SSO integration options and implementation timeline>

# KEY ENTITIES
# Important people, organizations, and concepts related to this user.
- { name: TechCorp Solutions, types: [organization,company], summary: Emily's company with 500+ employees, currently on Enterprise plan since November 2024 }
- { name: API Integration Team, types: [organization,team], summary: Emily's department responsible for integrating external APIs and managing technical implementations }
- { name: Enterprise Plan, types: [product,subscription], summary: Premium subscription tier with advanced features including SSO, webhooks, and priority support }
- { name: John Smith, types: [person,colleague], summary: Emily's technical team member who collaborates on API integration projects }
- { name: Sarah Chen, types: [person,account_manager], summary: Emily's dedicated account manager who handles enterprise support and feature requests }
```

## Update a template

Update an existing template's content:

```python Python
from zep_cloud import Zep

client = Zep(api_key="YOUR_API_KEY")

client.context.update_context_template(
    template_id="customer-support",
    template="""# CUSTOMER PROFILE
# A summary of who this user is and their background.
%{user_summary}

# FACTS
# Key facts and relationships extracted from conversations.
%{edges limit=20}

# ACCOUNT DETAILS
# Subscription and payment information for this account.
%{entities types=[subscription,payment_method]}

# TEAM MEMBERS
# People associated with this user's organization.
%{entities limit=10 types=[person]}

# OBSERVATIONS
# Durable, evidence-backed patterns Zep has derived about this user.
%{observations limit=5}

# RECENT CONVERSATIONS
# Per-thread summaries of what was discussed and resolved.
%{thread_summaries limit=3}"""
)
```

```typescript TypeScript
import { Zep } from "@getzep/zep-cloud";

const client = new Zep({ apiKey: "YOUR_API_KEY" });

await client.context.updateContextTemplate("customer-support", {
    template: `# CUSTOMER PROFILE
# A summary of who this user is and their background.
%{user_summary}

# FACTS
# Key facts and relationships extracted from conversations.
%{edges limit=20}

# ACCOUNT DETAILS
# Subscription and payment information for this account.
%{entities types=[subscription,payment_method]}

# TEAM MEMBERS
# People associated with this user's organization.
%{entities limit=10 types=[person]}

# OBSERVATIONS
# Durable, evidence-backed patterns Zep has derived about this user.
%{observations limit=5}

# RECENT CONVERSATIONS
# Per-thread summaries of what was discussed and resolved.
%{thread_summaries limit=3}`
});
```

```go Go
import (
    "context"
    zep "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/context"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey("YOUR_API_KEY"),
)

_, err := client.UpdateContextTemplate(
    context.TODO(),
    "customer-support",
    &zep.UpdateContextTemplateRequest{
        Template: `# CUSTOMER PROFILE
# A summary of who this user is and their background.
%{user_summary}

# FACTS
# Key facts and relationships extracted from conversations.
%{edges limit=20}

# ACCOUNT DETAILS
# Subscription and payment information for this account.
%{entities types=[subscription,payment_method]}

# TEAM MEMBERS
# People associated with this user's organization.
%{entities limit=10 types=[person]}

# OBSERVATIONS
# Durable, evidence-backed patterns Zep has derived about this user.
%{observations limit=5}

# RECENT CONVERSATIONS
# Per-thread summaries of what was discussed and resolved.
%{thread_summaries limit=3}`,
    },
)
```

## Read a template

Retrieve a specific template by its ID or list all templates:

```python Python
from zep_cloud import Zep

client = Zep(api_key="YOUR_API_KEY")

# Get a specific template
template = client.context.get_context_template(template_id="customer-support")

# List all templates
templates = client.context.list_context_templates()
```

```typescript TypeScript
import { Zep } from "@getzep/zep-cloud";

const client = new Zep({ apiKey: "YOUR_API_KEY" });

// Get a specific template
const template = await client.context.getContextTemplate("customer-support");

// List all templates
const templates = await client.context.listContextTemplates();
```

```go Go
import (
    "context"
    zepclient "github.com/getzep/zep-go/v3/context"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey("YOUR_API_KEY"),
)

// Get a specific template
template, err := client.GetContextTemplate(context.TODO(), "customer-support")

// List all templates
templates, err := client.ListContextTemplates(context.TODO())
```

## Delete a template

Delete a template when it's no longer needed:

```python Python
from zep_cloud import Zep

client = Zep(api_key="YOUR_API_KEY")

client.context.delete_context_template(template_id="customer-support")
```

```typescript TypeScript
import { Zep } from "@getzep/zep-cloud";

const client = new Zep({ apiKey: "YOUR_API_KEY" });

await client.context.deleteContextTemplate("customer-support");
```

```go Go
import (
    "context"
    zepclient "github.com/getzep/zep-go/v3/context"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey("YOUR_API_KEY"),
)

err := client.DeleteContextTemplate(context.TODO(), "customer-support")
```