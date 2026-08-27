> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# User Summary Instructions

Get started with the example in the video using:

```bash
git clone https://github.com/getzep/zep.git
cd zep/examples/python/user-summary-instructions-example
```

## Overview

User summary instructions customize how Zep generates the entity summary for each user in their Context Graph. You can create up to 5 custom instructions per user, set of users, or project-wide. Each instruction consists of a `name` (unique identifier) and `text` (the instruction content, maximum 100 characters).

**User summary and the user node**

Each user has a single unique user node in their graph representing the user themselves. The user summary generated from these instructions lives on this user node. You can retrieve the user node and its summary using the `get_node` method shown in the SDK reference.

## Default instructions

Zep applies the following default instructions to generate user summaries when no custom instructions are specified:

1. What are the user's key personal and lifestyle details?
2. What are the user's important relationships or social connections?
3. What does the user do for work, study, or main pursuits?
4. What are the user's preferences, values, and recurring goals?
5. What procedural or interaction instructions has the user given for how the AI should assist them?

These default instructions ensure comprehensive user summaries that capture essential information across personal, professional, and interaction contexts.

## Custom instructions

Instructions are managed through dedicated methods that allow you to add, list, and delete them. You can apply instructions to specific users by providing user IDs, or set them as project-wide defaults by omitting user IDs.

**Best practices for writing instructions**: Instructions should be focused and specific, designed to elicit responses that can be answered in a sentence or two. Phrasing instructions as questions is often an effective way to get accurate and succinct responses.

User summary instructions do not apply to data ingested through the [Batch API](/adding-batch-data) (or the deprecated `graph.add_batch()` and `thread.add_messages_batch()` methods).

```python Python
from zep_cloud.client import Zep
from zep_cloud.types import UserInstruction

client = Zep(api_key=API_KEY)

# Add instructions for specific users
client.user.add_user_summary_instructions(
    instructions=[
        UserInstruction(
            name="professional_background",
            text="What are the user's key professional skills and career achievements?",
        )
    ],
    user_ids=[user_id],
)

# Add project-wide default instructions (applied to all users without custom instructions)
client.user.add_user_summary_instructions(
    instructions=[
        UserInstruction(
            name="communication_style",
            text="How does the user prefer to receive information and assistance?",
        )
    ],
)

# List instructions for a user
instructions = client.user.list_user_summary_instructions(user_id=user_id)

# Delete specific instructions for a user
client.user.delete_user_summary_instructions(
    instruction_names=["professional_background"],
    user_ids=[user_id],
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";

const client = new ZepClient({
  apiKey: API_KEY,
});

// Add instructions for specific users
await client.user.addUserSummaryInstructions({
  instructions: [
    {
      name: "professional_background",
      text: "What are the user's key professional skills and career achievements?",
    }
  ],
  userIds: [userId],
});

// Add project-wide default instructions (applied to all users without custom instructions)
await client.user.addUserSummaryInstructions({
  instructions: [
    {
      name: "communication_style",
      text: "How does the user prefer to receive information and assistance?",
    }
  ],
});

// List instructions for a user
const instructions = await client.user.listUserSummaryInstructions({
  userId: userId,
});

// Delete specific instructions for a user
await client.user.deleteUserSummaryInstructions({
  instructionNames: ["professional_background"],
  userIds: [userId],
});
```

```go Go
import (
	"context"
	"log"

	"github.com/getzep/zep-go/v3"
	zepclient "github.com/getzep/zep-go/v3/client"
	"github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(option.WithAPIKey(apiKey))

// Add instructions for specific users
_, err := client.User.AddUserSummaryInstructions(context.TODO(), &zep.ApidataAddUserInstructionsRequest{
	Instructions: []*zep.UserInstruction{
		{
			Name: "professional_background",
			Text: "What are the user's key professional skills and career achievements?",
		},
	},
	UserIDs: []string{userID},
})
if err != nil {
	log.Fatalf("Failed to add user summary instructions: %v", err)
}

// Add project-wide default instructions (applied to all users without custom instructions)
_, err = client.User.AddUserSummaryInstructions(context.TODO(), &zep.ApidataAddUserInstructionsRequest{
	Instructions: []*zep.UserInstruction{
		{
			Name: "communication_style",
			Text: "How does the user prefer to receive information and assistance?",
		},
	},
})
if err != nil {
	log.Fatalf("Failed to add default instructions: %v", err)
}

// List instructions for a user
instructions, err := client.User.ListUserSummaryInstructions(context.TODO(), &zep.ApidataListUserInstructionsRequest{
	UserID: userID,
})
if err != nil {
	log.Fatalf("Failed to list instructions: %v", err)
}

// Delete specific instructions for a user
_, err = client.User.DeleteUserSummaryInstructions(context.TODO(), &zep.ApidataDeleteUserInstructionsRequest{
	InstructionNames: []string{"professional_background"},
	UserIDs:          []string{userID},
})
if err != nil {
	log.Fatalf("Failed to delete instructions: %v", err)
}
```

## Utilizing user summary

User summaries are automatically included in [Zep's Context Block](/retrieving-context#zeps-context-block). You can toggle whether the user summary is included in the Context Block on the Projects page of the web app. Accounts created before November 10, 2025 will need to enable this setting manually, while new accounts have it enabled by default.

Alternatively, you can build a custom context block by retrieving the user node and including its summary. See [this example](/cookbook/advanced-context-block-construction#example-4-using-user-summary-in-context-block) for a complete implementation.