> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Custom instructions

Available to [Flex Plus and Enterprise](https://www.getzep.com/pricing) customers.

## Why use custom instructions

Zep's graph extraction uses general-purpose logic by default. Custom instructions let you describe the domain your application operates in, including specialized terminology and concepts that Zep might not otherwise understand. This domain context helps Zep interpret data more accurately during extraction.

Custom instructions describe **your domain** — the terminology, concepts, and context Zep needs to understand your data. If you need to define specific **entity types or relationship types** for your graph, use [custom ontology](/customizing-graph-structure#custom-entity-and-edge-types) instead.

## How custom instructions work

Custom instructions are applied **automatically in the background** whenever data is added to a graph. There is no parameter on `thread.add_messages`, `graph.add`, or any other ingestion method to select which instructions to use — Zep fetches and applies the relevant instructions based on the target graph.

### Resolution order

When data is ingested into a graph, Zep determines which instructions to use in the following order:

1. **Graph-specific instructions** — If the target graph has instructions set via `user_ids` or `graph_ids`, those are used.
2. **Project-wide defaults** — If no graph-specific instructions exist, Zep falls back to project-wide default instructions.
3. **Built-in extraction logic** — If no custom instructions are defined at all, Zep uses its general-purpose extraction logic.

This means you can set broad project-wide instructions as a baseline and override them for specific users or graphs when needed.

## Defining custom instructions

### Project-wide instructions

When you omit `user_ids` and `graph_ids`, instructions are added as project-wide defaults. These apply to all graphs that don't have their own graph-specific instructions.

```python
from zep_cloud import Zep, CustomInstruction

client = Zep(api_key="YOUR_API_KEY")

# Add project-wide custom instructions
client.graph.add_custom_instructions(
    instructions=[
        CustomInstruction(
            name="legal_domain",
            text=(
                "This application operates in the legal domain. "
                "Common legal terminology includes: consideration, "
                "estoppel, tort, indemnification, force majeure, "
                "severability, arbitration clause, non-compete, "
                "and confidentiality. A 'party' refers to a person "
                "or organization involved in a legal agreement. "
                "'Clauses' are specific provisions within a contract."
            )
        )
    ]
)
```

```typescript
import { Zep } from "@getzep/zep-cloud";

const client = new Zep({ apiKey: "YOUR_API_KEY" });

// Add project-wide custom instructions
await client.graph.addCustomInstructions({
    instructions: [
        {
            name: "legal_domain",
            text:
                "This application operates in the legal domain. " +
                "Common legal terminology includes: consideration, " +
                "estoppel, tort, indemnification, force majeure, " +
                "severability, arbitration clause, non-compete, " +
                "and confidentiality. A 'party' refers to a person " +
                "or organization involved in a legal agreement. " +
                "'Clauses' are specific provisions within a contract."
        }
    ]
});
```

```go
import (
    "context"
    zep "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey("YOUR_API_KEY"),
)

// Add project-wide custom instructions
_, err := client.Graph.AddCustomInstructions(
    context.TODO(),
    &zep.AddCustomInstructionsRequest{
        Instructions: []*zep.CustomInstruction{
            {
                Name: "legal_domain",
                Text: "This application operates in the legal domain. " +
                    "Common legal terminology includes: consideration, " +
                    "estoppel, tort, indemnification, force majeure, " +
                    "severability, arbitration clause, non-compete, " +
                    "and confidentiality. A 'party' refers to a person " +
                    "or organization involved in a legal agreement. " +
                    "'Clauses' are specific provisions within a contract.",
            },
        },
    },
)
if err != nil {
    log.Fatal("Error adding custom instructions:", err)
}
```

When you add data to any graph, Zep automatically applies these project-wide instructions. No extra parameters are needed:

```python Python
# Add data to a graph — the legal_domain instructions apply automatically
client.graph.add(
    graph_id="contract_reviews",
    type="text",
    data=(
        "The licensing agreement between Acme Corp and GlobalTech Inc "
        "includes a non-compete clause effective for 24 months and an "
        "arbitration clause requiring disputes be resolved in New York."
    )
)
```

```typescript TypeScript
// Add data to a graph — the legal_domain instructions apply automatically
await client.graph.add({
    graphId: "contract_reviews",
    type: "text",
    data:
        "The licensing agreement between Acme Corp and GlobalTech Inc " +
        "includes a non-compete clause effective for 24 months and an " +
        "arbitration clause requiring disputes be resolved in New York."
});
```

```go Go
// Add data to a graph — the legal_domain instructions apply automatically
graphID := "contract_reviews"
_, err = client.Graph.Add(context.TODO(), &zep.AddDataRequest{
    GraphID: &graphID,
    Type:    zep.GraphDataTypeText,
    Data: "The licensing agreement between Acme Corp and GlobalTech Inc " +
        "includes a non-compete clause effective for 24 months and an " +
        "arbitration clause requiring disputes be resolved in New York.",
})
if err != nil {
    log.Fatal("Error adding data:", err)
}
```

### Graph-specific instructions

To add instructions for specific users or graphs, provide `user_ids` or `graph_ids`. These override any project-wide defaults for the specified graphs.

```python
from zep_cloud import Zep, CustomInstruction

client = Zep(api_key="YOUR_API_KEY")

# Add instructions for specific users
client.graph.add_custom_instructions(
    user_ids=["user_123", "user_456"],
    instructions=[
        CustomInstruction(
            name="healthcare_domain",
            text=(
                "This application operates in the healthcare domain. "
                "Common medical terminology includes: prognosis (expected "
                "outcome), etiology (cause of a condition), contraindication "
                "(reason to avoid a treatment), comorbidity (co-occurring "
                "conditions), and differential diagnosis (distinguishing "
                "between conditions with similar symptoms). A 'prescription' "
                "refers to a specific medication, dosage, and schedule."
            )
        )
    ]
)
```

```typescript
import { Zep } from "@getzep/zep-cloud";

const client = new Zep({ apiKey: "YOUR_API_KEY" });

// Add instructions for specific users
await client.graph.addCustomInstructions({
    userIds: ["user_123", "user_456"],
    instructions: [
        {
            name: "healthcare_domain",
            text:
                "This application operates in the healthcare domain. " +
                "Common medical terminology includes: prognosis (expected " +
                "outcome), etiology (cause of a condition), contraindication " +
                "(reason to avoid a treatment), comorbidity (co-occurring " +
                "conditions), and differential diagnosis (distinguishing " +
                "between conditions with similar symptoms). A 'prescription' " +
                "refers to a specific medication, dosage, and schedule."
        }
    ]
});
```

```go
import (
    "context"
    zep "github.com/getzep/zep-go/v3"
    zepclient "github.com/getzep/zep-go/v3/client"
    "github.com/getzep/zep-go/v3/option"
)

client := zepclient.NewClient(
    option.WithAPIKey("YOUR_API_KEY"),
)

// Add instructions for specific users
_, err := client.Graph.AddCustomInstructions(
    context.TODO(),
    &zep.AddCustomInstructionsRequest{
        UserIDs: []string{"user_123", "user_456"},
        Instructions: []*zep.CustomInstruction{
            {
                Name: "healthcare_domain",
                Text: "This application operates in the healthcare domain. " +
                    "Common medical terminology includes: prognosis (expected " +
                    "outcome), etiology (cause of a condition), contraindication " +
                    "(reason to avoid a treatment), comorbidity (co-occurring " +
                    "conditions), and differential diagnosis (distinguishing " +
                    "between conditions with similar symptoms). A 'prescription' " +
                    "refers to a specific medication, dosage, and schedule.",
            },
        },
    },
)
if err != nil {
    log.Fatal("Error adding custom instructions:", err)
}
```

When you add messages to a thread belonging to one of these users, Zep automatically applies the `healthcare_domain` instructions. No extra parameters are needed:

```python Python
from zep_cloud.types import Message

# Add messages — the healthcare_domain instructions apply automatically
response = client.thread.add_messages(
    thread_id,
    messages=[
        Message(
            name="Dr. Patel",
            role="user",
            content="Patient shows signs of acute bronchitis with a secondary comorbidity of asthma. Prescribing azithromycin 500mg for 3 days.",
        ),
        Message(
            name="Assistant",
            role="assistant",
            content="Noted. Should I flag any contraindications with the patient's existing medications?",
        ),
        Message(
            name="Dr. Patel",
            role="user",
            content="Good point — check against the current prednisone prescription. The prognosis is good if we manage both conditions.",
        ),
    ]
)
```

```typescript TypeScript
import type { Message } from "@getzep/zep-cloud/api";

// Add messages — the healthcare_domain instructions apply automatically
const response = await client.thread.addMessages(threadId, {
    messages: [
        {
            name: "Dr. Patel",
            role: "user",
            content: "Patient shows signs of acute bronchitis with a secondary comorbidity of asthma. Prescribing azithromycin 500mg for 3 days.",
        },
        {
            name: "Assistant",
            role: "assistant",
            content: "Noted. Should I flag any contraindications with the patient's existing medications?",
        },
        {
            name: "Dr. Patel",
            role: "user",
            content: "Good point — check against the current prednisone prescription. The prognosis is good if we manage both conditions.",
        },
    ]
});
```

```go Go
// Add messages — the healthcare_domain instructions apply automatically
response, err := client.Thread.AddMessages(
    context.TODO(),
    threadID,
    &v3.AddThreadMessagesRequest{
        Messages: []*v3.Message{
            {
                Name:    v3.String("Dr. Patel"),
                Role:    "user",
                Content: "Patient shows signs of acute bronchitis with a secondary comorbidity of asthma. Prescribing azithromycin 500mg for 3 days.",
            },
            {
                Name:    v3.String("Assistant"),
                Role:    "assistant",
                Content: "Noted. Should I flag any contraindications with the patient's existing medications?",
            },
            {
                Name:    v3.String("Dr. Patel"),
                Role:    "user",
                Content: "Good point — check against the current prednisone prescription. The prognosis is good if we manage both conditions.",
            },
        },
    },
)
if err != nil {
    log.Fatal("Error adding messages:", err)
}
```

## Important behaviors

### Upsert behavior

Adding an instruction with an existing name updates the instruction text rather than creating a duplicate. This allows you to refine instructions over time without manually deleting the old version first.

## Limits

| Limit                    | Value              |
| ------------------------ | ------------------ |
| Instructions per request | 5                  |
| Instruction name length  | 100 characters     |
| Instruction text length  | 1-5,000 characters |
| User IDs per request     | 50                 |
| Graph IDs per request    | 50                 |