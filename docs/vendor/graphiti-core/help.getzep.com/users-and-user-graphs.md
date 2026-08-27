> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Users and User Graphs

## Overview

A User represents an individual interacting with your application. Each User can have multiple Threads associated with them, allowing you to track and manage their interactions over time. Additionally, each user has an associated User Graph which stores the context for that user.

## Users

The unique identifier for each user is their `UserID`. This can be any string value, such as a username, email address, or UUID.

**Users Enable Simple User Privacy Management**

Deleting a User will delete all Threads and thread artifacts associated with that User with a single API call, making it easy to handle Right To Be Forgotten requests.

### Ensuring Your User Data Is Correctly Mapped to the Knowledge Graph

Adding your user's `email`, `first_name`, and `last_name` ensures that chat messages and business data are correctly mapped to the user node in the Zep knowledge graph.

For example, if business data contains your user's email address, it will be related directly to the user node.

You can associate rich business context with a User:

* `user_id`: A unique identifier of the user that maps to your internal User ID.
* `email`: The user's email.
* `first_name`: The user's first name.
* `last_name`: The user's last name.

### UserGroups

You can collect users into [UserGroups](/usergroup-access) and attach [policy-based access control](/policy-based-access-control) policies to a group. Every user in the group inherits its grants, which is how you give a set of Memory MCP users access to specific standalone graphs. A UserGroup is a security grouping of users; it is separate from the user graph, which stores each user's context.

## User Graphs

Each user has an associated User Graph that stores their context across all threads. This graph-based context system provides several important capabilities:

### Cross-Thread Context Integration

The knowledge graph does not separate the data from different threads, but integrates the data together to create a unified picture of the user. So the `thread.get_user_context` method doesn't return context derived only from that thread, but instead returns whatever user-level context is most relevant to that thread, based on the thread's most recent messages.

This means that insights and information learned in one conversation thread are automatically available in all other threads for the same user, creating a coherent and continuous context experience.

### Privacy and RTBF Capabilities

When you delete a user, all associated data is removed:

* All threads belonging to that user
* All thread artifacts (messages, metadata)
* The entire user graph and all knowledge extracted from conversations

This single-operation approach makes it simple to handle Right To Be Forgotten (RTBF) requests and comply with privacy regulations.

### Default Ontology for User Graphs

User graphs utilize Zep's default ontology, consisting of default entity types and default edge types that affect how the graph is built. You can read more about default and custom graph ontology in the [Customizing Graph Structure](/customizing-graph-structure) guide.

Each user graph comes with default entity and edge types that help classify and structure information extracted from conversations. You can also disable the default entity and edge types for specific users if you need precise control over your graph structure.

### The User Node

**User summary and the user node**

Each user has a single unique user node in their graph representing the user themselves. The [user summary](/user-summary) generated from user summary instructions lives on this user node. You can retrieve the user node and its summary using the `get_node` method described in the SDK reference.

The user node serves as a central hub in the knowledge graph, connecting all information about that user. It stores a high-level [user summary](/user-summary) — a persistent, baseline picture of who the user is, included by default in every Context Block. Its content can be steered through [User Summary Instructions](/user-summary-instructions).

## Next Steps

Now that you understand how Users and User Graphs work together, you can:

* Learn about [Threads](/threads) and how they relate to users
* Discover how to [add messages to threads](/adding-messages)
* Learn how to [retrieve context for your agent](/retrieving-context)
* Read about the [User Summary](/user-summary) included by default in every Context Block
* Explore [customizing user summaries](/user-summary-instructions)
* Understand more about the [graph](/graph-overview)