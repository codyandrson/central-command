> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Share Memory Across Users Using Group Graphs

In this recipe, we will demonstrate how to share memory across different users by utilizing group graphs. We will set up a user session, add group-specific data, and integrate the OpenAI client to show how to use both user and group memory to enhance the context of a chatbot.

First, we initialize the Zep client, create a user, and create a session:

```python
# Initialize the Zep client
zep_client = AsyncZep(api_key="YOUR_API_KEY")  # Ensure your API key is set appropriately

# Add one example user
user_id = uuid.uuid4().hex
await zep_client.user.add(
    user_id=user_id,
    email="cookbook@example.com"
)

# Create a new session for the user
session_id = uuid.uuid4().hex
await zep_client.memory.add_session(
    session_id=session_id,
    user_id=user_id,
)
```

Next, we create a new group and add structured business data to the graph, in the form of a JSON string. This step uses the [groups API](/groups) and the [graph API](/adding-data-to-the-graph):

```python
group_id = uuid.uuid4().hex
await zep_client.group.add(group_id=group_id)

product_json_data = [
    {
        "type": "Sedan",
        "gas_mileage": "25 mpg",
        "maker": "Toyota"
    },
    # ... more cars
]

json_string = json.dumps(product_json_data)
await zep_client.graph.add(
    group_id=group_id,
    type="json",
    data=json_string,
)
```

Finally, we initialize the OpenAI client and define a `chatbot_response` function that retrieves user and group memory, constructs a system/developer message, and generates a contextual response. This leverages the [memory API](/concepts#using-memoryget), [graph API](/searching-the-graph), and the OpenAI chat completions endpoint.

```python
# Initialize the OpenAI client
oai_client = OpenAI()

async def chatbot_response(user_message, session_id):
    # Retrieve user memory
    user_memory = await zep_client.memory.get(session_id)

    # Search the group graph using the user message as the query
    results = await zep_client.graph.search(group_id=group_id, query=user_message, scope="edges")
    relevant_group_edges = results.edges
    product_context_string = "Below are some facts related to our car inventory that may help you respond to the user: \n"
    for edge in relevant_group_edges:
        product_context_string += f"{edge.fact}\n"

    # Combine context strings for the developer message
    developer_message = f"You are a helpful chat bot assistant for a car sales company. Answer the user's message while taking into account the following background information:\n{user_memory.context}\n{product_context_string}"

    # Generate a response using the OpenAI API
    completion = oai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "developer", "content": developer_message},
            {"role": "user", "content": user_message}
        ]
    )
    response = completion.choices[0].message

    # Add the conversation to memory
    messages = [
        Message(role="user", role_type="user", content=user_message),
        Message(role="assistant", role_type="assistant", content=response)
    ]
    await zep_client.memory.add(session_id, messages=messages)

    return response
```

This recipe demonstrated how to share memory across users by utilizing group graphs with Zep. We set up user sessions, added structured group data, and integrated the OpenAI client to generate contextual responses, providing a robust approach to memory sharing across different users.