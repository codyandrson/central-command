> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Building a Chatbot with Zep

For an introduction to Zep's memory layer, Knowledge Graph, and other key concepts, see the [Concepts Guide](/concepts).

A Jupyter notebook version of this guide is [available here](https://github.com/getzep/zep/blob/main/examples/python/quickstart/quickstart.ipynb).

In this guide, we'll walk through a simple example of how to use Zep Cloud to build a chatbot. We're going to upload a number of datasets to Zep, building a graph of data about a user.

Then we'll use the Zep Python SDK to retrieve and search the data.

Finally, we'll build a simple chatbot that uses Zep to retrieve and search data to respond to a user.

## Set up your environment

1. Sign up for a [Zep Cloud](https://www.getzep.com/) account.

2. Ensure you install required dependencies into your Python environment before running this notebook. See [Installing Zep SDKs](sdks.mdx) for more information. Optionally create your environment in a `virtualenv`.

```bash
pip install zep-cloud openai rich python-dotenv
```

3. Ensure that you have a `.env` file in your working directory that includes your `ZEP_API_KEY` and `OPENAI_API_KEY`:

Zep API keys are specific to a project. You can create multiple keys for a
single project. Visit `Project Settings` in the Zep dashboard to manage your
API keys.

```text
ZEP_API_KEY=<key>
OPENAI_API_KEY=<key>
```

```python Python
import os
import json
import uuid

from openai import OpenAI
import rich

from dotenv import load_dotenv
from zep_cloud.client import Zep
from zep_cloud import Message

load_dotenv()

zep = Zep(api_key=os.environ.get("ZEP_API_KEY"))

oai_client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
)
```

```typescript TypeScript
import { ZepClient } from "@getzep/zep-cloud";
import * as dotenv from "dotenv";
import { v4 as uuidv4 } from 'uuid';
import OpenAI from 'openai';

dotenv.config();

const zep = new ZepClient({ apiKey: process.env.ZEP_API_KEY });

const oai_client = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
});
```

We also provide an [Asynchronous Python client](/install-sdks#initialize-the-client).

## Create User and add a Session

Users in Zep may have one or more chat sessions. These are threads of messages between the user and an agent.

Include the user's **full name** and **email address** when creating a user.
This improves Zep's ability to associate data, such as emails or documents,
with a user.

```python Python
bot_name = "SupportBot"
user_name = "Emily"
user_id = user_name + str(uuid.uuid4())[:4]
session_id = str(uuid.uuid4())

zep.user.add(
    user_id=user_id,
    email=f"{user_name}@painters.com",
    first_name=user_name,
    last_name="Painter",
)

zep.memory.add_session(
    user_id=user_id,
    session_id=session_id,
)
```

```typescript TypeScript
const bot_name = "SupportBot";
const user_name = "Emily";
const user_id = user_name + uuidv4().substring(0, 4);
const session_id = uuidv4();

await zep.user.add({
  userId: user_id,
  email: `${user_name}@painters.com`,
  firstName: user_name,
  lastName: "Painter",
});

await zep.memory.addSession({
  userId: user_id,
  sessionId: session_id,
});
```

## Datasets

We're going to use the [memory](/concepts#using-memoryadd) and [graph](/adding-data-to-the-graph) APIs to upload an assortment of data to Zep. These include past dialog with the agent, CRM support cases, and billing data.

```python Python
support_cases = [
    {
        "subject": "Bug: Magic Pen Tool Drawing Goats Instead of Boats",
        "messages": [
            {
                "role": "user",
                "content": "Whenever I use the magic pen tool to draw boats, it ends up drawing goats instead.",
                "timestamp": "2024-03-16T14:20:00Z",
            },
            {
                "role": "support_agent",
                "content": f"Hi {user_name}, that sounds like a bug! Thanks for reporting it. Could you let me know exactly how you're using the tool when this happens?",
                "timestamp": "2024-03-16T14:22:00Z",
            },
            {
                "role": "user",
                "content": "Sure, I select the magic pen, draw a boat shape, and it just replaces the shape with goats.",
                "timestamp": "2024-03-16T14:25:00Z",
            },
            {
                "role": "support_agent",
                "content": "Got it! We'll escalate this to our engineering team. In the meantime, you can manually select the boat shape from the options rather than drawing it with the pen.",
                "timestamp": "2024-03-16T14:27:00Z",
            },
            {
                "role": "user",
                "content": "Okay, thanks. I hope it gets fixed soon!",
                "timestamp": "2024-03-16T14:30:00Z",
            },
        ],
        "status": "escalated",
    },
]

chat_history = [
    {
        "role": "assistant",
        "name": bot_name,
        "content": f"Hello {user_name}, welcome to PaintWiz support. How can I assist you today?",
        "timestamp": "2024-03-15T10:00:00Z",
    },
    {
        "role": "user",
        "name": user_name,
        "content": "I'm absolutely furious! Your AI art generation is completely broken!",
        "timestamp": "2024-03-15T10:02:00Z",
    },
    {
        "role": "assistant",
        "name": bot_name,
        "content": f"I'm sorry to hear that you're experiencing issues, {user_name}. Can you please provide more details about what's going wrong?",
        "timestamp": "2024-03-15T10:03:00Z",
    },
    {
        "role": "user",
        "name": user_name,
        "content": "Every time I try to draw mountains, your stupid app keeps turning them into fountains! And what's worse, all the people in my drawings have six fingers! It's ridiculous!",
        "timestamp": "2024-03-15T10:05:00Z",
    },
    {
        "role": "assistant",
        "name": bot_name,
        "content": f"I sincerely apologize for the frustration this is causing you, {user_name}. That certainly sounds like a significant glitch in our system. I understand how disruptive this can be to your artistic process. Can you tell me which specific tool or feature you're using when this occurs?",
        "timestamp": "2024-03-15T10:06:00Z",
    },
    {
        "role": "user",
        "name": user_name,
        "content": "I'm using the landscape generator and the character creator. Both are completely messed up. How could you let this happen?",
        "timestamp": "2024-03-15T10:08:00Z",
    },
]

transactions = [
    {
        "date": "2024-07-30",
        "amount": 99.99,
        "status": "Success",
        "account_id": user_id,
        "card_last_four": "1234",
    },
    {
        "date": "2024-08-30",
        "amount": 99.99,
        "status": "Failed",
        "account_id": user_id,
        "card_last_four": "1234",
        "failure_reason": "Card expired",
    },
    {
        "date": "2024-09-15",
        "amount": 99.99,
        "status": "Failed",
        "account_id": user_id,
        "card_last_four": "1234",
        "failure_reason": "Card expired",
    },
]

account_status = {
    "user_id": user_id,
    "account": {
        "account_id": user_id,
        "account_status": {
            "status": "suspended",
            "reason": "payment failure",
        },
    },
}

def convert_to_zep_messages(chat_history: list[dict[str, str | None]]) -> list[Message]:
    """
    Convert chat history to Zep messages.

    Args:
    chat_history (list): List of dictionaries containing chat messages.

    Returns:
    list: List of Zep Message objects.
    """
    return [
        Message(
            role_type=msg["role"],
            role=msg.get("name", None),
            content=msg["content"],
        )
        for msg in chat_history
    ]

# Zep's high-level API allows us to add a list of messages to a session.
zep.memory.add(
    session_id=session_id, messages=convert_to_zep_messages(chat_history)
)

# The lower-level data API allows us to add arbitrary data to a user's Knowledge Graph.
for tx in transactions:
    zep.graph.add(user_id=user_id, data=json.dumps(tx), type="json")

    zep.graph.add(
        user_id=user_id, data=json.dumps(account_status), type="json"
    )

for case in support_cases:
    zep.graph.add(user_id=user_id, data=json.dumps(case), type="json")
```

```typescript TypeScript
const support_cases = [
  {
    subject: "Bug: Magic Pen Tool Drawing Goats Instead of Boats",
    messages: [
      {
        role: "user",
        content: "Whenever I use the magic pen tool to draw boats, it ends up drawing goats instead.",
        timestamp: "2024-03-16T14:20:00Z",
      },
      {
        role: "support_agent",
        content: `Hi ${user_name}, that sounds like a bug! Thanks for reporting it. Could you let me know exactly how you're using the tool when this happens?`,
        timestamp: "2024-03-16T14:22:00Z",
      },
      {
        role: "user",
        content: "Sure, I select the magic pen, draw a boat shape, and it just replaces the shape with goats.",
        timestamp: "2024-03-16T14:25:00Z",
      },
      {
        role: "support_agent",
        content: "Got it! We'll escalate this to our engineering team. In the meantime, you can manually select the boat shape from the options rather than drawing it with the pen.",
        timestamp: "2024-03-16T14:27:00Z",
      },
      {
        role: "user",
        content: "Okay, thanks. I hope it gets fixed soon!",
        timestamp: "2024-03-16T14:30:00Z",
      },
    ],
    status: "escalated",
  },
];

const chat_history = [
  {
    role: "assistant",
    name: bot_name,
    content: `Hello ${user_name}, welcome to PaintWiz support. How can I assist you today?`,
    timestamp: "2024-03-15T10:00:00Z",
  },
  {
    role: "user",
    name: user_name,
    content: "I'm absolutely furious! Your AI art generation is completely broken!",
    timestamp: "2024-03-15T10:02:00Z",
  },
  {
    role: "assistant",
    name: bot_name,
    content: `I'm sorry to hear that you're experiencing issues, ${user_name}. Can you please provide more details about what's going wrong?`,
    timestamp: "2024-03-15T10:03:00Z",
  },
  {
    role: "user",
    name: user_name,
    content: "Every time I try to draw mountains, your stupid app keeps turning them into fountains! And what's worse, all the people in my drawings have six fingers! It's ridiculous!",
    timestamp: "2024-03-15T10:05:00Z",
  },
  {
    role: "assistant",
    name: bot_name,
    content: `I sincerely apologize for the frustration this is causing you, ${user_name}. That certainly sounds like a significant glitch in our system. I understand how disruptive this can be to your artistic process. Can you tell me which specific tool or feature you're using when this occurs?`,
    timestamp: "2024-03-15T10:06:00Z",
  },
  {
    role: "user",
    name: user_name,
    content: "I'm using the landscape generator and the character creator. Both are completely messed up. How could you let this happen?",
    timestamp: "2024-03-15T10:08:00Z",
  },
];

const transactions = [
  {
    date: "2024-07-30",
    amount: 99.99,
    status: "Success",
    account_id: user_id,
    card_last_four: "1234",
  },
  {
    date: "2024-08-30",
    amount: 99.99,
    status: "Failed",
    account_id: user_id,
    card_last_four: "1234",
    failure_reason: "Card expired",
  },
  {
    date: "2024-09-15",
    amount: 99.99,
    status: "Failed",
    account_id: user_id,
    card_last_four: "1234",
    failure_reason: "Card expired",
  },
];

const account_status = {
  user_id: user_id,
  account: {
    account_id: user_id,
    account_status: {
      status: "suspended",
      reason: "payment failure",
    },
  },
};

/**
 * Convert chat history to Zep messages.
 * 
 * Args:
 * chatHistory (array): Array of objects containing chat messages.
 * 
 * Returns:
 * array: Array of Zep message objects.
 */
const convertToZepMessages = (chatHistory: any[]) => {
  return chatHistory.map(msg => ({
    roleType: msg.role,
    role: msg.name || null,
    content: msg.content,
  }));
};

// Zep's high-level API allows us to add a list of messages to a session.
await zep.memory.add(session_id, {
  messages: convertToZepMessages(chat_history)
});

// The lower-level data API allows us to add arbitrary data to a user's Knowledge Graph.
for (const tx of transactions) {
  await zep.graph.add({
    userId: user_id,
    type: "json",
    data: JSON.stringify(tx)
  });

  await zep.graph.add({
    userId: user_id,
    type: "json",
    data: JSON.stringify(account_status)
  });
}

for (const case_data of support_cases) {
  await zep.graph.add({
    userId: user_id,
    type: "json",
    data: JSON.stringify(case_data)
  });
}
```

### Wait a minute or two!

We've batch uploaded a number of datasets that need to be ingested into Zep's
graph before they can be queried. In ordinary operation, this data would
stream into Zep and ingestion latency would be negligible.

## Retrieve data from Zep

We'll start with getting a list of facts, which are stored on the edges of the graph. We'll see the temporal data associated with facts as well as the graph nodes the fact is related to.

This data is also viewable in the Zep Web application.

```python Python
all_user_edges = zep.graph.edge.get_by_user_id(user_id=user_id)
rich.print(all_user_edges[:3])
```

```typescript TypeScript
const all_user_edges = await zep.graph.edge.getByUserId(user_id);
console.log(all_user_edges.slice(0, 3));
```

```text
[
    EntityEdge(
        created_at='2025-02-20T20:31:01.769332Z',
        episodes=['0d3a35c7-ebd3-427d-89a6-1a8dabd2df64'],
        expired_at='2025-02-20T20:31:18.742184Z',
        fact='The transaction failed because the card expired.',
        invalid_at='2024-09-15T00:00:00Z',
        name='HAS_FAILURE_REASON',
        source_node_uuid='06c61c00-9101-474f-9bca-42b4308ec378',
        target_node_uuid='07efd834-f07a-4c3c-9b32-d2fd9362afd5',
        uuid_='fb5ee0df-3aa0-44f3-889d-5bb163971b07',
        valid_at='2024-08-30T00:00:00Z',
        graph_id='8e5686fc-f175-4da9-8778-ad8d60fc469a'
    ),
    EntityEdge(
        created_at='2025-02-20T20:31:33.771557Z',
        episodes=['60d1d20e-ed6c-4966-b1da-3f4ca274a524'],
        expired_at=None,
        fact='Emily uses the magic pen tool to draw boats.',
        invalid_at=None,
        name='USES_TOOL',
        source_node_uuid='36f5c5c6-eb16-4ebb-9db0-fd34809482f5',
        target_node_uuid='e337522d-3a62-4c45-975d-904e1ba25667',
        uuid_='f9eb0a98-1624-4932-86ca-be75a3c248e5',
        valid_at='2025-02-20T20:29:40.217412Z',
        graph_id='8e5686fc-f175-4da9-8778-ad8d60fc469a'
    ),
    EntityEdge(
        created_at='2025-02-20T20:30:28.499178Z',
        episodes=['b8e4da4c-dd5e-4c48-bdbc-9e6568cd2d2e'],
        expired_at=None,
        fact="SupportBot understands how disruptive the glitch in the AI art generation can be to Emily's artistic process.",
        invalid_at=None,
        name='UNDERSTANDS',
        source_node_uuid='fd4ab1f0-e19e-40b7-aaec-78bd97571725',
        target_node_uuid='8e5686fc-f175-4da9-8778-ad8d60fc469a',
        uuid_='f8c52a21-e938-46a3-b930-04671d0c018a',
        valid_at='2025-02-20T20:29:39.08846Z',
        graph_id='8e5686fc-f175-4da9-8778-ad8d60fc469a'
    )
]
```

The high-level [memory API](/concepts#using-memoryget) provides an easy way to retrieve memory relevant to the current conversation by using the last 4 messages and their proximity to the User node.

The `memory.get` method is a good starting point for retrieving relevant conversation context. It shortcuts passing recent messages to the `graph.search` API and returns a [context string](/concepts#memory-context), raw facts, and historical chat messages, providing everything needed for your agent's prompts.

```python Python
memory = zep.memory.get(session_id=session_id)
rich.print(memory.context)
```

```typescript TypeScript
const memory = await zep.memory.get(session_id);
console.log(memory.context);
```

```text
FACTS and ENTITIES represent relevant context to the current conversation.

# These are the most relevant facts and their valid date ranges
# format: FACT (Date range: from - to)
<FACTS>
  - SupportBot understands how disruptive the glitch in the AI art generation can be to Emily's artistic process. (2025-02-20 20:29:39 - present)
  - SupportBot sincerely apologizes to Emily for the frustration caused by the issues with the AI art generation. (2025-02-20 20:29:39 - present)
  - Emily has contacted SupportBot for assistance regarding issues she is experiencing. (2025-02-20 20:29:39 - present)
  - The user Emily reported a bug regarding the magic pen tool drawing goats instead of boats. (2024-03-16 14:20:00 - present)
  - The bug report has been escalated to the engineering team. (2024-03-16 14:27:00 - present)
  - Emily is a user of the AI art generation. (2025-02-20 20:29:39 - present)
  - user has the name of Emily Painter (2025-02-20 20:29:39 - present)
  - Emily5e57 is using the landscape generator. (2025-02-20 20:29:39 - 2025-02-20 20:29:39)
  - user has the id of Emily5e57 (2025-02-20 20:29:39 - present)
  - user has the email of Emily@painters.com (2025-02-20 20:29:39 - present)
  - Emily is furious about the stupid app. (2025-02-20 20:29:39 - present)
  - Emily claims that the AI art generation is completely broken. (2025-02-20 20:29:39 - present)
</FACTS>

# These are the most relevant entities
# ENTITY_NAME: entity summary
<ENTITIES>
  - Emily Painter: Emily Painter contacted PaintWiz support for assistance, where she was welcomed by the support bot that inquired about the specific issues she was facing to provide better help.
  - Emily@painters.com: user with the email of Emily@painters.com
  - Emily5e57: Emily5e57, a user of the PaintWiz AI art generation tool, successfully processed a transaction of $99.99 on July 30, 2024, using a card ending in '1234'. However, she is experiencing
significant frustration with the application due to malfunctions, such as the landscape generator incorrectly transforming mountains into fountains and characters being depicted with six fingers. 
These issues have led her to question the reliability of the tool, and she considers it to be completely broken. Emily has reached out to PaintWiz support for assistance, as these problems are 
severely disrupting her artistic process.
  - PaintWiz support: PaintWiz is an AI art generation platform that provides tools for users to create art. Recently, a user named Emily reported significant issues with the service, claiming that
the AI art generation is not functioning properly. The support bot responded to her concerns, apologizing for the disruption to her artistic process and asking for more details about the specific 
tool or feature she was using. This interaction highlights PaintWiz's commitment to customer support, as they actively seek to assist users with their inquiries and problems related to their 
products.
  - SupportBot: A support agent named Emily addressed a user's report about a bug in a drawing application where the magic pen tool incorrectly produced goats instead of boats. After confirming the
issue, she escalated it to the engineering team and suggested a temporary workaround of manually selecting the boat shape. Meanwhile, SupportBot, a virtual assistant for PaintWiz, also assisted 
another user named Emily who was frustrated with the AI art generation feature, acknowledging her concerns and requesting more details to help resolve the problem.
  - AI art generation: Emily, a user, expressed her frustration regarding the AI art generation, stating that it is completely broken.
  - options: The user reported a bug with the magic pen tool, stating that when attempting to draw boats, the tool instead draws goats. The support agent acknowledged the issue and requested more 
details about how the user was utilizing the tool. The user explained that they select the magic pen and draw a boat shape, but it gets replaced with goats. The support agent confirmed they would 
escalate the issue to the engineering team and suggested that the user manually select the boat shape from the options instead of drawing it with the pen. The user expressed hope for a quick 
resolution.
</ENTITIES>
```

```python Python
rich.print(memory.messages)
```

```typescript TypeScript
console.log(memory.messages);
```

```text
[
    Message(
        content='Hello Emily, welcome to PaintWiz support. How can I assist you today?',
        created_at='2025-02-20T20:29:39.08846Z',
        metadata=None,
        role='SupportBot',
        role_type='assistant',
        token_count=0,
        updated_at='0001-01-01T00:00:00Z',
        uuid_='e2b86f93-84d6-4270-adbc-e421f39b6f90'
    ),
    Message(
        content="I'm absolutely furious! Your AI art generation is completely broken!",
        created_at='2025-02-20T20:29:39.08846Z',
        metadata=None,
        role='Emily',
        role_type='user',
        token_count=0,
        updated_at='0001-01-01T00:00:00Z',
        uuid_='ec39e501-6dcc-4f8c-b300-f586d66005d8'
    )
]
```

We can also use the [graph API](/searching-the-graph) to search edges/facts for arbitrary text. This API offers more options, including the ability to search node summaries and various re-rankers.

```python Python
r = zep.graph.search(user_id=user_id, query="Why are there so many goats?", limit=4, scope="edges")
rich.print(r.edges)
```

```typescript TypeScript
const r = await zep.graph.search({
  userId: user_id,
  query: "Why are there so many goats?",
  limit: 4,
  scope: "edges"
});
console.log(r.edges);
```

```text
[
    EntityEdge(
        created_at='2025-02-20T20:31:33.771566Z',
        episodes=['60d1d20e-ed6c-4966-b1da-3f4ca274a524'],
        expired_at=None,
        fact='The magic pen tool draws goats instead of boats when used by Emily.',
        invalid_at=None,
        name='DRAWS_INSTEAD_OF',
        source_node_uuid='e337522d-3a62-4c45-975d-904e1ba25667',
        target_node_uuid='9814a57f-53a4-4d4a-ad5a-15331858ce18',
        uuid_='022687b6-ae08-4fef-9d6e-17afb07acdea',
        valid_at='2025-02-20T20:29:40.217412Z',
        graph_id='8e5686fc-f175-4da9-8778-ad8d60fc469a'
    ),
    EntityEdge(
        created_at='2025-02-20T20:31:33.771528Z',
        episodes=['60d1d20e-ed6c-4966-b1da-3f4ca274a524'],
        expired_at=None,
        fact='The user Emily reported a bug regarding the magic pen tool drawing goats instead of boats.',
        invalid_at=None,
        name='REPORTED_BY',
        source_node_uuid='36f5c5c6-eb16-4ebb-9db0-fd34809482f5',
        target_node_uuid='cff4e758-d1a4-4910-abe7-20101a1f0d77',
        uuid_='5c3124ec-b4a3-4564-a38f-02338e3db4c4',
        valid_at='2024-03-16T14:20:00Z',
        graph_id='8e5686fc-f175-4da9-8778-ad8d60fc469a'
    ),
    EntityEdge(
        created_at='2025-02-20T20:30:19.910797Z',
        episodes=['ff9eba8b-9e90-4765-a0ce-15eb44410f70'],
        expired_at=None,
        fact='The stupid app generates mountains.',
        invalid_at=None,
        name='GENERATES',
        source_node_uuid='b6e5a0ee-8823-4647-b536-5e6af0ba113a',
        target_node_uuid='43aaf7c9-628c-4bf0-b7cb-02d3e9c1a49c',
        uuid_='3514a3ad-1ed5-42c7-9f70-02834e8904bf',
        valid_at='2025-02-20T20:29:39.08846Z',
        graph_id='8e5686fc-f175-4da9-8778-ad8d60fc469a'
    ),
    EntityEdge(
        created_at='2025-02-20T20:30:19.910816Z',
        episodes=['ff9eba8b-9e90-4765-a0ce-15eb44410f70'],
        expired_at=None,
        fact='The stupid app keeps turning mountains into fountains.',
        invalid_at=None,
        name='TRANSFORMS_INTO',
        source_node_uuid='43aaf7c9-628c-4bf0-b7cb-02d3e9c1a49c',
        target_node_uuid='0c90b42c-2b9f-4998-aa67-cc968f9002d3',
        uuid_='2f113810-3597-47a4-93c5-96d8002366fa',
        valid_at='2025-02-20T20:29:39.08846Z',
        graph_id='8e5686fc-f175-4da9-8778-ad8d60fc469a'
    )
]
```

## Creating a simple Chatbot

In the next cells, Emily starts a new chat session with a support agent and complains that she can't log in. Our simple chatbot will, given relevant facts retrieved from Zep's graph, respond accordingly.

Here, the support agent is provided with Emily's billing information and account status, which Zep retrieves as most relevant to Emily's login issue.

```python Python
new_session_id = str(uuid.uuid4())

emily_message = "Hi, I can't log in!"

# We start a new session indicating that Emily has started a new chat with the support agent.
zep.memory.add_session(user_id=user_id, session_id=new_session_id)

# We need to add the Emily's message to the session in order for memory.get to return
# relevant facts related to the message
zep.memory.add(
    session_id=new_session_id,
    messages=[Message(role_type="user", role=user_name, content=emily_message)],
)
```

```typescript TypeScript
const new_session_id = uuidv4();
const emily_message = "Hi, I can't log in!";

// We start a new session indicating that Emily has started a new chat with the support agent.
await zep.memory.addSession({
  userId: user_id,
  sessionId: new_session_id
});

// We need to add the Emily's message to the session in order for memory.get to return
// relevant facts related to the message
await zep.memory.add(new_session_id, {
  messages: [{
    roleType: "user",
    role: user_name,
    content: emily_message
  }]
});
```

```python Python
system_message = """
You are a customer support agent. Carefully review the facts about the user below and respond to the user's question.
Be helpful and friendly.
"""

memory = zep.memory.get(session_id=new_session_id)

messages = [
    {
        "role": "system",
        "content": system_message,
    },
    {
        "role": "assistant",
        # The context field is an opinionated string that contains facts and entities relevant to the current conversation.
        "content": memory.context,
    },
    {
        "role": "user",
        "content": emily_message,
    },
]

response = oai_client.chat.completions.create(
    model="gpt-4o-mini",
    messages=messages,
    temperature=0,
)

print(response.choices[0].message.content)
```

```typescript TypeScript
const system_message = `
You are a customer support agent. Carefully review the facts about the user below and respond to the user's question.
Be helpful and friendly.
`;

const new_memory = await zep.memory.get(new_session_id);

const messages = [
  {
    role: "system" as const,
    content: system_message,
  },
  {
    role: "assistant" as const,
    // The context field is an opinionated string that contains facts and entities relevant to the current conversation.
    content: new_memory.context || "",
  },
  {
    role: "user" as const,
    content: emily_message,
  },
];

const response = await oai_client.chat.completions.create({
  model: "gpt-4o-mini",
  messages: messages,
  temperature: 0,
});

console.log(response.choices[0].message.content);
```

```text
Hi Emily! I'm here to help you. It looks like your account is currently suspended due to a payment failure. This might be the reason you're unable to log in. 

The last transaction on your account failed because the card you were using has expired. If you update your payment information, we can help you get your account reactivated. Would you like assistance with that?
```

Let's look at the memory context string Zep retrieved for the above `memory.get` call.

```python Python
rich.print(memory.context)
```

```typescript TypeScript
console.log(new_memory.context);
```

```text
FACTS and ENTITIES represent relevant context to the current conversation.

# These are the most relevant facts and their valid date ranges
# format: FACT (Date range: from - to)
<FACTS>
  - Account with ID 'Emily1c2e' has a status of 'suspended'. (2025-02-24 23:24:29 - present)
  - user has the id of Emily1c2e (2025-02-24 23:24:29 - present)
  - User with ID 'Emily1c2e' has an account with ID 'Emily1c2e'. (2025-02-24 23:24:29 - present)
  - The bug report has been escalated to the engineering team. (2024-03-16 14:27:00 - present)
  - user has the name of Emily Painter (2025-02-24 23:24:29 - present)
  - Emily is the person being assisted by SupportBot. (2025-02-24 23:24:28 - present)
  - Emily1c2e is using the character creator. (2025-02-24 23:24:28 - present)
  - The reason for the account status 'suspended' is 'payment failure'. (2025-02-24 23:24:29 - present)
  - SupportBot is part of PaintWiz support. (2025-02-24 23:24:28 - present)
  - user has the email of Emily@painters.com (2025-02-24 23:24:29 - present)
  - Emily is a user of PaintWiz. (2025-02-24 23:24:28 - present)
  - The support agent suggested that Emily manually select the boat shape from the options. (2025-02-24 23:24:29 - 
present)
  - All the people in Emily1c2e's drawings have six fingers. (2025-02-24 23:24:28 - present)
  - Emily1c2e is using the landscape generator. (2025-02-24 23:24:28 - present)
  - Emily is a user of the AI art generation. (2025-02-24 23:24:28 - present)
  - Emily states that the AI art generation is completely broken. (2025-02-24 23:24:28 - present)
  - The magic pen tool draws goats instead of boats when used by Emily. (2025-02-24 23:24:29 - present)
  - Emily1c2e tries to draw mountains. (2025-02-24 23:24:28 - present)
</FACTS>

# These are the most relevant entities
# ENTITY_NAME: entity summary
<ENTITIES>
  - goats: In a recent support interaction, a user reported a bug with the magic pen tool in a drawing application,
where attempting to draw boats resulted in the tool drawing goats instead. The user, Emily, described the issue, 
stating that whenever she selects the magic pen and draws a boat shape, it is replaced with a goat shape. The 
support agent acknowledged the problem and confirmed it would be escalated to the engineering team for resolution. 
In the meantime, the agent suggested that Emily could manually select the boat shape from the available options 
instead of using the pen tool. Emily expressed her hope for a quick fix to the issue.
  - failure_reason: Two transactions failed due to expired cards: one on September 15, 2024, and another on August 
30, 2024, for the amount of $99.99 associated with account ID 'Emily1c2e'.
  - status: User account "Emily1c2e" is suspended due to a payment failure. A transaction of $99.99 on September 
15, 2024, failed because the card ending in "1234" had expired. This card had previously been used successfully for
the same amount on July 30, 2024, but a failure on August 30, 2024, resulted in the account's suspension.
  - bug: A user reported a bug with the magic pen tool, stating that when attempting to draw boats, the tool 
instead draws goats. The support agent acknowledged the issue and requested more details about how the user was 
utilizing the tool. The user explained that they select the magic pen and draw a boat shape, but it gets replaced 
with goats. The support agent confirmed the bug and stated that it would be escalated to the engineering team for 
resolution. In the meantime, they suggested that the user manually select the boat shape from the options instead 
of using the pen. The user expressed hope for a quick fix.
  - user_id: Emily reported a bug with the magic pen tool in a drawing application, where attempting to draw boats 
resulted in goats being drawn instead. A support agent acknowledged the issue and requested more details. Emily 
explained her process, and the agent confirmed the bug, stating it would be escalated to the engineering team. As a
temporary workaround, the agent suggested manually selecting the boat shape. Emily expressed hope for a quick 
resolution. Additionally, it was noted that another user, identified as "Emily1c2e," has a suspended account due to
a payment failure.
  - people: Emily is frustrated with the AI art generation feature of PaintWiz, specifically mentioning that the 
people in her drawings are depicted with six fingers, which she finds ridiculous.
  - character creator: Emily is experiencing significant issues with the character creator feature of the app. She 
reports that when using the landscape generator and character creator, the app is malfunctioning, resulting in 
bizarre outcomes such as people in her drawings having six fingers. Emily expresses her frustration, stating that 
the AI art generation is completely broken and is not functioning as expected.
</ENTITIES>
```