> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Utilizing Facts and Summaries

## Understanding Facts and Summaries in Zep

### Facts are Precise and Time-Stamped Information

A `fact` is stored on an [edge](/sdk-reference/graph/edge/get) and captures a detailed relationship about specific events. It includes `valid_at` and `invalid_at` timestamps, ensuring temporal accuracy and preserving a clear history of changes over time. This makes facts reliable sources of truth for critical information retrieval, providing the authoritative context needed for accurate decision-making and analysis by your agent.

### Summaries are High-Level Overviews of Entities or Concepts

A `summary` resides on a [node](/sdk-reference/graph/node/get) and provides a broad snapshot of an entity or concept and its relationships to other nodes. Summaries offer an aggregated and concise representation, making it easier to understand key information at a glance.

#### Choosing Between Facts and Summaries

Zep does not recommend relying solely on summaries for grounding LLM responses. While summaries provide a high-level overview, the [Context Block](/concepts#memory-context) should be used since it includes relevant facts (each with valid and invalid timestamps). This ensures that conversations are based on up-to-date and contextually accurate information.

## Context String

When calling [Get Session Memory](/sdk-reference/memory/get), Zep employs a sophisticated search strategy to surface the most pertinent information. The system first examines recent context by analyzing the last 4 messages (2 complete chat turns). It then utilizes multiple search techniques, with reranking steps to identify and prioritize the most contextually significant details for the current conversation.

The returned, `context` is structured as a string, optimized for language model prompts, making it easy to integrate into AI workflows. For more details, see [Key Concepts](/concepts#memory-context). In addition to the `context`, the API response includes an array of the identified `relevant_facts` with their supporting details.

## Rating Facts for Relevancy

Not all `relevant_facts` are equally important to your specific use-case. For example, a relationship coach app may need to recall important facts about a user’s family, but what the user ate for breakfast Friday last week is unimportant.

Fact ratings are a way to help Zep understand the importance of `relevant_facts` to your particular use case. After implementing fact ratings, you can specify a `minRating` when retrieving `relevant_facts` from Zep, ensuring that the memory `context` string contains customized content.

### Implementing Fact Ratings

The `fact_rating_instruction` framework consists of an instruction and three example facts, one for each of a `high`, `medium`, and `low` rating.  These are passed when [Adding a User](/sdk-reference/user/add) or [Adding a Group](/sdk-reference/group/add) and become a property of the User or Group.

### Example: Fact Rating Implementation

```python Rating Facts for Poignancy
fact_rating_instruction = """Rate the facts by poignancy. Highly poignant 
facts have a significant emotional impact or relevance to the user. 
Facts with low poignancy are minimally relevant or of little emotional
significance."""
fact_rating_examples = FactRatingExamples(
    high="The user received news of a family member's serious illness.",
    medium="The user completed a challenging marathon.",
    low="The user bought a new brand of toothpaste.",
)
client.user.add(
    user_id=user_id,
    fact_rating_instruction=FactRatingInstruction(
        instruction=fact_rating_instruction,
        examples=fact_rating_examples,
    ),
)
```

```python Use Case-Specific Fact Rating
client.user.add(
    user_id=user_id,
    fact_rating_instruction=FactRatingInstruction(
        instruction="""Rate the facts by how relevant they 
                       are to purchasing shoes.""",
        examples=FactRatingExamples(
            high="The user has agreed to purchase a Reebok running shoe.",
            medium="The user prefers running to cycling.",
            low="The user purchased a dress.",
        ),
    ),
)
```

All facts are rated on a scale between 0 and 1.  You can access `rating` when retrieving `relevant_facts` from [Get Session Memory](/sdk-reference/memory/get).

### Limiting Memory Recall to High-Rating Facts

You can filter `relevant_facts` by setting the `minRating` parameter in [Get Session Memory](/sdk-reference/memory/get).

```python
result = client.memory.get(session_id, min_rating=0.7)
```

## Adding or Deleting Facts or Summaries

Facts and summaries are generated as part of the ingestion process. If you follow the directions for [adding data to the graph](/adding-data-to-the-graph), new facts and summaries will be created.

Deleting facts and summaries is handled by deleting data from the graph. Facts and summaries will be deleted when you [delete the edge or node](/deleting-data-from-the-graph) they exist on.

## APIs related to Facts and Summaries

You can extract facts and summaries using the following methods:

| Method                                                                                                                                                                                                     | Description                                                                                |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| [Get Session Memory](/sdk-reference/memory/get)                                                                                                                                                            | Retrieves the `context` string and `relevant_facts`                                        |
| [Add User](/sdk-reference/user/add) <br /> [Update User](/sdk-reference/user/update) <br /> [Create Group](/sdk-reference/group/add) <br /> [Update Group](/sdk-reference/group/update)                    | Allows specifying `fact_rating_instruction`                                                |
| [Get User](/sdk-reference/user/get)   <br /> [Get Users](/sdk-reference/user/list-ordered) <br /> [Get Group](/sdk-reference/group/get-group) <br /> [Get All Groups](/sdk-reference/group/get-all-groups) | Retrieves `fact_rating_instruction` for each user or group                                 |
| [Search the Graph](/sdk-reference/graph/search)                                                                                                                                                            | Returns a list. Each item is an `edge` or `node` and has an associated `fact` or `summary` |
| [Get User Edges](/sdk-reference/graph/edge/get-by-user-id) <br /> [Get Group Edges](/sdk-reference/graph/edge/get-by-group-id) <br /> [Get Edge](/sdk-reference/graph/edge/get)                            | Retrieves `fact` on each `edge`                                                            |
| [Get User Nodes](/sdk-reference/graph/node/get-by-user-id) <br /> [Get Group Nodes](/sdk-reference/graph/node/get-by-group-id) <br /> [Get Node](/sdk-reference/graph/node/get)                            | Retrieves `summary` on each `node`                                                         |