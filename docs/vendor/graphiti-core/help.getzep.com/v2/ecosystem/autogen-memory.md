> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Autogen Memory Example

Autogen 4 has been released. This example is not compatible with Autogen 4. We'll be updating it soon!

**NOTE** This example does not include all code required to run the Autogen Agents.

A complete Notebook example of using Zep for Autogen long-term memory may be found in the [Zep Python SDK Repository](https://github.com/getzep/zep/blob/main/examples/python/autogen-agent/agent.ipynb).

This example walks through how to build an Autogen Agent with long-term memory. Zep builds a knowledge graph from user interactions with the agent, enabling the agent to recall relevant facts from previous conversations or user interactions.

In this example we will:

* Create an Autogen Agent class that extends `ConversableAgent` by adding long-term memory
* Create a Mental Health Assistant Agent, CareBot, that acts as a counselor and coach.
* Create a user Agent, Cathy, who stands in for our expected user.
* Demonstrate preloading chat history into Zep.
* Demonstrate the agents in conversation, with CareBot recalling facts from previous conversations with Cathy.
* Inspect Facts within Zep, and demonstrate how to use Zep's Fact Ratings to improve the quality of returned facts.

## Install dependencies

```bash
pip install autogen zep-cloud
```

## Import Autogen and configure define a `config_list`

```python
import os
from dotenv import load_dotenv
import uuid
from typing import Union, Dict
from autogen import ConversableAgent, Agent

load_dotenv()

config_list = [
    {
        "model": "gpt-4o-mini",
        "api_key": os.environ.get("OPENAI_API_KEY"),
        "max_tokens": 1024,
    }
]
```

## initiualize the Zep Client

You can sign up for a Zep account here: [https://www.getzep.com/](https://www.getzep.com/)

```python
from zep_cloud.client import AsyncZep
from zep_cloud import Message, FactRatingExamples, FactRatingInstruction

MIN_FACT_RATING = 0.3

# Configure Zep
zep = AsyncZep(api_key=os.environ.get("ZEP_API_KEY"))
```

## ZepConversableAgent

The `ZepConversableAgent` is a custom implementation of the Autogen `ConversableAgent` that integrates with Zep for long-term memory management. This class extends the functionality of the base `ConversableAgent` by adding Zep-specific features for persisting and retrieving facts from long-term memory.

```python
class ZepConversableAgent(ConversableAgent):
    """
    A custom ConversableAgent that integrates with Zep for long-term memory.
    """
    def __init__(
        self,
        name: str,
        system_message: str,
        llm_config: dict,
        function_map: dict,
        human_input_mode: str,
        zep_session_id: str,
    ):
        super().__init__(
            name=name,
            system_message=system_message,
            llm_config=llm_config,
            function_map=function_map,
            human_input_mode=human_input_mode,
        )
        self.zep_session_id = zep_session_id
        # store the original system message as we will update it with relevant facts from Zep
        self.original_system_message = system_message
        self.register_hook(
            "a_process_last_received_message", self.persist_user_messages
        )
        self.register_hook(
            "a_process_message_before_send", self.persist_assistant_messages
        )

    async def persist_assistant_messages(
        self, sender: Agent, message: Union[Dict, str], recipient: Agent, silent: bool
    ):
        """Agent sends a message to the user. Add the message to Zep."""

        # Assume message is a string
        zep_messages = convert_to_zep_messages(
            [{"role": "assistant", "name": self.name, "content": message}]
        )
        await zep.memory.add(session_id=self.zep_session_id, messages=zep_messages)

        return message

    async def persist_user_messages(self, messages: list[dict[str, str]] | str):
        """
        User sends a message to the agent. Add the message to Zep and
        update the system message with relevant facts from Zep.
        """
        # Assume messages is a string
        zep_messages = convert_to_zep_messages([{"role": "user", "content": messages}])
        await zep.memory.add(session_id=self.zep_session_id, messages=zep_messages)

        memory = await zep.memory.get(self.zep_session_id, min_rating=MIN_FACT_RATING)

        # Update the system message with the relevant facts retrieved from Zep
        self.update_system_message(
            self.original_system_message
            + f"\n\nRelevant facts about the user and their prior conversation:\n{memory.relevant_facts}"
        )

        return messages
```

## Zep User and Session Management

### Zep User

A [Zep User](/v2/users) represents an individual interacting with your application. Each User can have multiple Sessions associated with them, allowing you to track and manage interactions over time. The unique identifier for each user is their `UserID`, which can be any string value (e.g., username, email address, or UUID).

### Zep Session

A [Zep Session](/v2/concepts) represents a conversation and can be associated with Users in a one-to-many relationship. Chat messages are added to Sessions, with each session having many messages.

### Fact Rating

[Fact Rating](/v2/facts) is a feature in Zep that allows you to rate the importance or relevance of facts extracted from conversations. This helps in prioritizing and filtering information when retrieving memory artifacts. Here, we rate facts based on poignancy. We provide a definition of poignancy and several examples of highly poignant and low-poignancy facts. When retrieving memory, you can use the `min_rating` parameter to filter facts based on their importance.

Fact Rating helps ensure the most relevant information, especially in long or complex conversations, is used to ground the agent.

```python
bot_name = "CareBot"
user_name = "Cathy"

user_id = user_name + str(uuid.uuid4())[:4]
session_id = str(uuid.uuid4())

fact_rating_instruction = """Rate the facts by poignancy. Highly poignant
facts have a significant emotional impact or relevance to the user.
Low poignant facts are minimally relevant or of little emotional
significance."""
fact_rating_examples = FactRatingExamples(
    high="The user received news of a family member's serious illness.",
    medium="The user completed a challenging marathon.",
    low="The user bought a new brand of toothpaste.",
)

await zep.user.add(user_id=user_id,
fact_rating_instruction=FactRatingInstruction(
        instruction=fact_rating_instruction,
        examples=fact_rating_examples,
    )
)
```

## Preload a prior conversation into Zep

We'll load a prior conversation into long-term memory. We'll use facts derived from this conversation when Cathy restarts the conversation with CareBot, ensuring Carebot has context.

```python
chat_history = [
    {
        "role": "assistant",
        "name": "carebot",
        "content": "Hi Cathy, how are you doing today?",
    },
    {
        "role": "user",
        "name": "Cathy",
        "content": "To be honest, I've been feeling a bit down and demotivated lately. It's been tough.",
    },
    {
        "role": "assistant",
        "name": "CareBot",
        "content": "I'm sorry to hear that you're feeling down and demotivated, Cathy. It's understandable given the challenges you're facing. Can you tell me more about what's been going on?",
    },
    {
        "role": "user",
        "name": "Cathy",
        "content": "Well, I'm really struggling to process the passing of my mother.",
    },
    {
        "role": "assistant",
        "name": "CareBot",
        "content": "I'm deeply sorry for your loss, Cathy. Losing a parent is incredibly difficult. It's normal to struggle with grief, and there's no 'right' way to process it. Would you like to talk about your mother or how you're coping?",
    },
    {
        "role": "user",
        "name": "Cathy",
        "content": "Yes, I'd like to talk about my mother. She was a kind and loving person.",
    },
]

# Convert chat history to Zep messages
zep_messages = convert_to_zep_messages(chat_history)

await zep.memory.add(session_id=session_id, messages=zep_messages)
```

## Review all facts in Zep

We query all session facts for this user session. Only facts that meet the `MIN_FACT_RATING` threshold are returned.

```python
response = await zep.memory.get(session_id=session_id, min_rating=MIN_FACT_RATING)

for r in response.relevant_facts:
    print(r)
```

```text
created_at='2024-10-07T20:04:11.98139Z' fact='Cathy has been feeling down and demotivated lately.' rating=0.5 uuid_='17183c18-381b-45d2-82ea-0c06317acf6f'
created_at='2024-10-07T20:04:11.98139Z' fact='Cathy describes her mother as a kind and loving person.' rating=0.5 uuid_='cd6b2e6d-b287-4d92-9de5-d4ee6e82111e'
created_at='2024-10-07T20:04:11.98139Z' fact='Cathy is struggling to process the passing of her mother.' rating=0.75 uuid_='bb2f100c-2f12-4976-9026-b322c29e457e'
```

## Create the Autogen agent, CareBot, an instance of `ZepConversableAgent`

We pass in the current `session_id` into the CareBot agent which allows it to retrieve relevant facts related to the conversation with Cathy.

```python
carebot_system_message = """
You are a compassionate mental health bot and caregiver. Review information about the user and their prior conversation below and respond accordingly.
Keep responses empathetic and supportive. And remember, always prioritize the user's well-being and mental health. Keep your responses very concise and to the point.
"""

agent = ZepConversableAgent(
    bot_name,
    system_message=carebot_system_message,
    llm_config={"config_list": config_list},
    function_map=None,  # No registered functions, by default it is None.
    human_input_mode="NEVER",  # Never ask for human input.
    zep_session_id=session_id,
)
```

## Create the Autogen agent, Cathy

Cathy is a stand-in for a human. When building a production application, you'd replace Cathy with a human-in-the-loop pattern.

**Note** that we're instructing Cathy to start the conversation with CareBit by asking about her previous session. This is an opportunity for us to test whether fact retrieval from Zep's long-term memory is working.

```python
cathy = ConversableAgent(
    user_name,
    system_message="You are a helpful mental health bot. You are seeking counsel from a mental health bot. Ask the bot about your previous conversation.",
    llm_config={"config_list": config_list},
    human_input_mode="NEVER",  # Never ask for human input.
)
```

## Start the conversation

We use Autogen's `a_initiate_chat` method to get the two agents conversing. CareBot is the primary agent.

**NOTE** how Carebot is able to recall the past conversation about Cathy's mother in detail, having had relevant facts from Zep added to its system prompt.

```python
result = await agent.a_initiate_chat(
    cathy,
    message="Hi Cathy, nice to see you again. How are you doing today?",
    max_turns=3,
)
```

```text
CareBot (to Cathy):

Hi Cathy, nice to see you again. How are you doing today?

--------------------------------------------------------------------------------
Cathy (to CareBot):

Hello CareBot! I'm doing well, thank you for asking. I wanted to reflect on our previous conversation—do you remember what we discussed? It would be helpful to revisit that topic and explore it further if you’re up for it!

--------------------------------------------------------------------------------
CareBot (to Cathy):

Of course, Cathy. We talked about your difficulty in processing your mother’s passing and how you've been feeling down and demotivated lately. It's completely natural to have these feelings, especially after such a significant loss. What would you like to explore further?

--------------------------------------------------------------------------------
Cathy (to CareBot):

Cathy: Thank you for reminding me, CareBot. Yes, I’ve been struggling with those feelings, and it’s been tough to navigate. I’d like to explore some coping strategies to help me process my grief. Are there any techniques or practices you would recommend?

--------------------------------------------------------------------------------
CareBot (to Cathy):

Absolutely, Cathy. Here are some coping strategies to help you navigate your grief:

1. **Journaling**: Writing your thoughts and feelings can be a great outlet and help you process your emotions.
2. **Talk to someone**: Sharing your feelings with a trusted friend or therapist can provide support and understanding.
3. **Mindfulness and meditation**: These practices can help ground you and create a sense of calm amid emotional turmoil.
4. **Create a tribute**: Honoring your mother through a scrapbook, writing letters, or lighting a candle can foster connection and memory.
5. **Physical activity**: Engaging in exercise can boost your mood and help alleviate stress.

Remember, it's important to be gentle with yourself as you navigate this process. What resonates with you?

--------------------------------------------------------------------------------
Cathy (to CareBot):

Cathy: Thank you for those suggestions, CareBot. I really like the idea of journaling and creating a tribute for my mother; it sounds like a meaningful way to express my feelings. I also think mindfulness could help me find some peace amidst the sadness. Do you have any specific tips on how to start journaling or practicing mindfulness?

--------------------------------------------------------------------------------
```

## Review current facts in Zep

Let's see how the facts have evolved as the conversation has progressed.

```python
response = await zep.memory.get(session_id, min_rating=MIN_FACT_RATING)

for r in response.relevant_facts:
    print(r)
```

```text
created_at='2024-10-07T20:04:28.397184Z' fact="Cathy wants to reflect on a previous conversation about her mother and explore the topic of her mother's passing further." rating=0.75 uuid_='56488eeb-d8ac-4b2f-8acc-75f71b56ad76'
created_at='2024-10-07T20:04:28.397184Z' fact='Cathy is struggling to process the passing of her mother and has been feeling down and demotivated lately.' rating=0.75 uuid_='0fea3f05-ed1a-4e39-a092-c91f8af9e501'
created_at='2024-10-07T20:04:28.397184Z' fact='Cathy describes her mother as a kind and loving person.' rating=0.5 uuid_='131de203-2984-4cba-9aef-e500611f06d9'
```

## Search over Facts in Zep's long-term memory

In addition to the `memory.get` method which uses the current conversation to retrieve relevant\_facts, we can also search Zep with our own keywords. Here, we retrieve facts using a query.

The `zep.graph.search` API may be used as an Agent tool, enabling an agent to search across user memory for facts.

```python
response = await zep.graph.search(
    query="What do you know about Cathy's family?",
    user_id=user_id,
)
relevant_edges = response.edges
formatted_facts = []
for edge in relevant_edges:
    valid_at = edge.valid_at if edge.valid_at is not None else "date unknown"
    invalid_at = edge.invalid_at if edge.invalid_at is not None else "present"
    formatted_fact = f"{edge.fact} (Date range: {valid_at} - {invalid_at})"
    formatted_facts.append(formatted_fact)

# Print the results
print("\nFound facts:")
for fact in formatted_facts:
    print(f"- {fact}")

```

```text
created_at='2024-10-07T20:04:28.397184Z' fact="Cathy wants to reflect on a previous conversation about her mother and explore the topic of her mother's passing further." rating=0.75 uuid_='56488eeb-d8ac-4b2f-8acc-75f71b56ad76'
created_at='2024-10-07T20:04:28.397184Z' fact='Cathy is struggling to process the passing of her mother and has been feeling down and demotivated lately.' rating=0.75 uuid_='0fea3f05-ed1a-4e39-a092-c91f8af9e501'
created_at='2024-10-07T20:04:28.397184Z' fact='Cathy describes her mother as a kind and loving person.' rating=0.5 uuid_='131de203-2984-4cba-9aef-e500611f06d9'
```