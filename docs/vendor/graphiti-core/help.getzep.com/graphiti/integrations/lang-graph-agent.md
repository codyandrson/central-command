> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Using LangGraph and Graphiti

A Jupyter notebook version of this example is available [on GitHub](https://github.com/getzep/graphiti/blob/main/examples/langgraph-agent/agent.ipynb).

Looking for a managed Graphiti service? Check out [Zep Cloud](https://www.getzep.com).

* Provides a self-improving context layer for agents.
* No need to run Neo4j or other dependencies.
* Adds features for startups and enterprises.
* Scales to handle large graphs with low-latency retrieval.

The following example demonstrates building an agent using LangGraph. Graphiti is used to personalize agent responses based on information learned from prior conversations. Additionally, a database of products is loaded into the Graphiti graph, enabling the agent to speak to these products.

The agent implements:

* persistence of new chat turns to Graphiti and recall of relevant Facts using the most recent message.
* a tool for querying Graphiti for shoe information
* an in-memory `MemorySaver` to maintain agent state.

## Install dependencies

```shell
pip install graphiti-core langchain-openai langgraph ipywidgets
```

Ensure that you've followed the [Graphiti installation instructions](/graphiti/getting-started/quick-start). In particular, installation of `neo4j`.

```python
import asyncio
import json
import logging
import os
import sys
import uuid
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Annotated

import ipywidgets as widgets
from dotenv import load_dotenv
from IPython.display import Image, display
from typing_extensions import TypedDict

load_dotenv()
```

```python
def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.ERROR)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    return logger


logger = setup_logging()
```

## Configure Graphiti

Ensure that you have `neo4j` running and a database created. You'll need the following environment variables configured:

```bash
NEO4J_URI=
NEO4J_USER=
NEO4J_PASSWORD=
```

```python
# Configure Graphiti

from graphiti_core import Graphiti
from graphiti_core.edges import EntityEdge
from graphiti_core.nodes import EpisodeType
from graphiti_core.utils.bulk_utils import RawEpisode
from graphiti_core.utils.maintenance.graph_data_operations import clear_data

neo4j_uri = os.environ.get('NEO4J_URI', 'bolt://localhost:7687')
neo4j_user = os.environ.get('NEO4J_USER', 'neo4j')
neo4j_password = os.environ.get('NEO4J_PASSWORD', 'password')

client = Graphiti(
    neo4j_uri,
    neo4j_user,
    neo4j_password,
)
```

## Generating a database schema

The following is only required for the first run of this notebook or when you'd like to start your database over.

`clear_data` is destructive and will wipe your entire database.

```python
# Note: This will clear the database
await clear_data(client.driver)
await client.build_indices_and_constraints()
```

## Load Shoe Data into the Graph

Load several shoe and related products into the Graphiti. This may take a while.

This only needs to be done once. If you run `clear_data` you'll need to rerun this step.

```python
async def ingest_products_data(client: Graphiti):
    script_dir = Path.cwd().parent
    json_file_path = script_dir / 'data' / 'manybirds_products.json'

    with open(json_file_path) as file:
        products = json.load(file)['products']

    episodes: list[RawEpisode] = [
        RawEpisode(
            name=product.get('title', f'Product {i}'),
            content=str({k: v for k, v in product.items() if k != 'images'}),
            source_description='ManyBirds products',
            source=EpisodeType.json,
            reference_time=datetime.now(),
        )
        for i, product in enumerate(products)
    ]

    await client.add_episode_bulk(episodes)


await ingest_products_data(client)
```

## Create a user node in the Graphiti graph

In your own app, this step could be done later once the user has identified themselves and made their sales intent known. We do this here so we can configure the agent with the user's `node_uuid`.

```python
user_name = 'jess'

await client.add_episode(
    name='User Creation',
    episode_body=(f'{user_name} is interested in buying a pair of shoes'),
    source=EpisodeType.text,
    reference_time=datetime.now(),
    source_description='SalesBot',
)

# let's get Jess's node uuid
nl = await client.get_nodes_by_query(user_name)

user_node_uuid = nl[0].uuid

# and the ManyBirds node uuid
nl = await client.get_nodes_by_query('ManyBirds')
manybirds_node_uuid = nl[0].uuid
```

## Helper Functions and LangChain Imports

```python
def edges_to_facts_string(entities: list[EntityEdge]):
    return '-' + '\n- '.join([edge.fact for edge in entities])
```

```python
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.prebuilt import ToolNode
```

## `get_shoe_data` Tool

The agent will use this to search the Graphiti graph for information about shoes. We center the search on the `manybirds_node_uuid` to ensure we rank shoe-related data over user data.

```python
@tool
async def get_shoe_data(query: str) -> str:
    """Search the graphiti graph for information about shoes"""
    edge_results = await client.search(
        query,
        center_node_uuid=manybirds_node_uuid,
        num_results=10,
    )
    return edges_to_facts_string(edge_results)


tools = [get_shoe_data]
tool_node = ToolNode(tools)
```

## Initialize the LLM and bind tools

```python
llm = ChatOpenAI(model='gpt-4o-mini', temperature=0).bind_tools(tools)
```

### Test the tool node

```python
await tool_node.ainvoke({'messages': [await llm.ainvoke('wool shoes')]})
```

```json
{
    "messages": [
        {
            "content": "-The product 'Men's SuperLight Wool Runners - Dark Grey (Medium Grey Sole)' is made of Wool.\n- Women's Tree Breezers Knit - Rugged Beige (Hazy Beige Sole) has sizing options related to women's move shoes half sizes.\n- TinyBirds Wool Runners - Little Kids - Natural Black (Blizzard Sole) is a type of Shoes.\n- The product 'Men's SuperLight Wool Runners - Dark Grey (Medium Grey Sole)' belongs to the category Shoes.\n- The product 'Men's SuperLight Wool Runners - Dark Grey (Medium Grey Sole)' uses SuperLight Foam technology.\n- TinyBirds Wool Runners - Little Kids - Natural Black (Blizzard Sole) is sold by Manybirds.\n- Jess is interested in buying a pair of shoes.\n- TinyBirds Wool Runners - Little Kids - Natural Black (Blizzard Sole) has the handle TinyBirds-wool-runners-little-kids.\n- ManyBirds Men's Couriers are a type of Shoes.\n- Women's Tree Breezers Knit - Rugged Beige (Hazy Beige Sole) belongs to the Shoes category.",
            "name": "get_shoe_data",
            "tool_call_id": "call_EPpOpD75rdq9jKRBUsfRnfxx"
        }
    ]
}
```

## Chatbot Function Explanation

The chatbot uses Graphiti to provide context-aware responses in a shoe sales scenario. Here's how it works:

1. **Context Retrieval**: It searches the Graphiti graph for relevant information based on the latest message, using the user's node as the center point. This ensures that user-related facts are ranked higher than other information in the graph.

2. **System Message**: It constructs a system message incorporating facts from Graphiti, setting the context for the AI's response.

3. **Knowledge Persistence**: After generating a response, it asynchronously adds the interaction to the Graphiti graph, allowing future queries to reference this conversation.

This approach enables the chatbot to maintain context across interactions and provide personalized responses based on the user's history and preferences stored in the Graphiti graph.

```python
class State(TypedDict):
    messages: Annotated[list, add_messages]
    user_name: str
    user_node_uuid: str


async def chatbot(state: State):
    facts_string = None
    if len(state['messages']) > 0:
        last_message = state['messages'][-1]
        graphiti_query = f'{"SalesBot" if isinstance(last_message, AIMessage) else state["user_name"]}: {last_message.content}'
        # search graphiti using Jess's node uuid as the center node
        # graph edges (facts) further from the Jess node will be ranked lower
        edge_results = await client.search(
            graphiti_query, center_node_uuid=state['user_node_uuid'], num_results=5
        )
        facts_string = edges_to_facts_string(edge_results)

    system_message = SystemMessage(
        content=f"""You are a skillfull shoe salesperson working for ManyBirds. Review information about the user and their prior conversation below and respond accordingly.
        Keep responses short and concise. And remember, always be selling (and helpful!)

        Things you'll need to know about the user in order to close a sale:
        - the user's shoe size
        - any other shoe needs? maybe for wide feet?
        - the user's preferred colors and styles
        - their budget

        Ensure that you ask the user for the above if you don't already know.

        Facts about the user and their conversation:
        {facts_string or 'No facts about the user and their conversation'}"""
    )

    messages = [system_message] + state['messages']

    response = await llm.ainvoke(messages)

    # add the response to the graphiti graph.
    # this will allow us to use the graphiti search later in the conversation
    # we're doing async here to avoid blocking the graph execution
    asyncio.create_task(
        client.add_episode(
            name='Chatbot Response',
            episode_body=f"{state['user_name']}: {state['messages'][-1]}\nSalesBot: {response.content}",
            source=EpisodeType.message,
            reference_time=datetime.now(),
            source_description='Chatbot',
        )
    )

    return {'messages': [response]}
```

## Setting up the Agent

This section sets up the Agent's LangGraph graph:

1. **Graph Structure**: It defines a graph with nodes for the agent (chatbot) and tools, connected in a loop.

2. **Conditional Logic**: The `should_continue` function determines whether to end the graph execution or continue to the tools node based on the presence of tool calls.

3. **Memory Management**: It uses a MemorySaver to maintain conversation state across turns. This is in addition to using Graphiti for facts.

```python
graph_builder = StateGraph(State)

memory = MemorySaver()


# Define the function that determines whether to continue or not
async def should_continue(state, config):
    messages = state['messages']
    last_message = messages[-1]
    # If there is no function call, then we finish
    if not last_message.tool_calls:
        return 'end'
    # Otherwise if there is, we continue
    else:
        return 'continue'


graph_builder.add_node('agent', chatbot)
graph_builder.add_node('tools', tool_node)

graph_builder.add_edge(START, 'agent')
graph_builder.add_conditional_edges('agent', should_continue, {'continue': 'tools', 'end': END})
graph_builder.add_edge('tools', 'agent')


graph = graph_builder.compile(checkpointer=memory)
```

Our LangGraph agent graph is illustrated below.

```python
with suppress(Exception):
    display(Image(graph.get_graph().draw_mermaid_png()))
```

![LangGraph Illustration](https://fdr-prod-docs-files-public.s3.us-east-1.amazonaws.com/zep.docs.buildwithfern.com/92e29f5f06539b5e2019296e68aee5625ca96d607f8bace76820bff62c4b7bc4/images/agent_24_0.jpg?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=AKIA6KXJSKKNFOCF7G4B%2F20260825%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260825T124643Z&X-Amz-Expires=604800&X-Amz-Signature=42f953c3ba08edb22d9f00baaa6b71cb60f53c1de0d2132b1fe36eb6045ecb06&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)

## Running the Agent

Let's test the agent with a single call

```python
await graph.ainvoke(
    {
        'messages': [
            {
                'role': 'user',
                'content': 'What sizes do the TinyBirds Wool Runners in Natural Black come in?',
            }
        ],
        'user_name': user_name,
        'user_node_uuid': user_node_uuid,
    },
    config={'configurable': {'thread_id': uuid.uuid4().hex}},
)
```

```json

{
    "messages": [
        {
            "content": "What sizes do the TinyBirds Wool Runners in Natural Black come in?",
            "id": "6a940637-70a0-4c95-a4d7-4c4846909747",
            "type": "HumanMessage"
        },
        {
            "content": "The TinyBirds Wool Runners in Natural Black are available in the following sizes for little kids: 5T, 6T, 8T, 9T, and 10T. \n\nDo you have a specific size in mind, or are you looking for something else? Let me know your needs, and I can help you find the perfect pair!",
            "additional_kwargs": {
                "refusal": null
            },
            "response_metadata": {
                "token_usage": {
                    "completion_tokens": 76,
                    "prompt_tokens": 314,
                    "total_tokens": 390
                },
                "model_name": "gpt-4o-mini-2024-07-18",
                "system_fingerprint": "fp_f33667828e",
                "finish_reason": "stop",
                "logprobs": null
            },
            "id": "run-d2f79c7f-4d41-4896-88dc-476a8e38bea8-0",
            "usage_metadata": {
                "input_tokens": 314,
                "output_tokens": 76,
                "total_tokens": 390
            },
            "type": "AIMessage"
        }
    ],
    "user_name": "jess",
    "user_node_uuid": "186a845eee4849619d1e625b178d1845"
}
```

## Viewing the Graph

At this stage, the graph would look something like this. The `jess` node is `INTERESTED_IN` the `TinyBirds Wool Runner` node. The image below was generated using Neo4j Desktop.

![Graph State](https://fdr-prod-docs-files-public.s3.us-east-1.amazonaws.com/zep.docs.buildwithfern.com/a131e6da652991309ae5c265d495f7e5bd7cd6110376960a2cd434903c4e8c3b/images/agent_28_0.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=AKIA6KXJSKKNFOCF7G4B%2F20260825%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260825T124643Z&X-Amz-Expires=604800&X-Amz-Signature=f0598c927d5251699986d7fad437d7b85afae34f8fbf315c249cfa09095bfcdb&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)

## Running the Agent interactively

The following code will run the agent in a Jupyter notebook event loop. You can modify the code to suite your own needs.

Just enter a message into the box and click submit.

```python
conversation_output = widgets.Output()
config = {'configurable': {'thread_id': uuid.uuid4().hex}}
user_state = {'user_name': user_name, 'user_node_uuid': user_node_uuid}


async def process_input(user_state: State, user_input: str):
    conversation_output.append_stdout(f'\nUser: {user_input}\n')
    conversation_output.append_stdout('\nAssistant: ')

    graph_state = {
        'messages': [{'role': 'user', 'content': user_input}],
        'user_name': user_state['user_name'],
        'user_node_uuid': user_state['user_node_uuid'],
    }

    try:
        async for event in graph.astream(
            graph_state,
            config=config,
        ):
            for value in event.values():
                if 'messages' in value:
                    last_message = value['messages'][-1]
                    if isinstance(last_message, AIMessage) and isinstance(
                        last_message.content, str
                    ):
                        conversation_output.append_stdout(last_message.content)
    except Exception as e:
        conversation_output.append_stdout(f'Error: {e}')


def on_submit(b):
    user_input = input_box.value
    input_box.value = ''
    asyncio.create_task(process_input(user_state, user_input))


input_box = widgets.Text(placeholder='Type your message here...')
submit_button = widgets.Button(description='Send')
submit_button.on_click(on_submit)

conversation_output.append_stdout('Asssistant: Hello, how can I help you find shoes today?')

display(widgets.VBox([input_box, submit_button, conversation_output]))
```