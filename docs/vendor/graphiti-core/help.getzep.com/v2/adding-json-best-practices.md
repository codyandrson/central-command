> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Adding JSON Best Practices

> Best practices for preparing JSON data for ingestion into Zep

Adding JSON to Zep without adequate preparation can lead to unexpected results. For instance, adding a large JSON without dividing it up can lead to a graph with very few nodes. Below, we go over what type of JSON works best with Zep, and techniques you can use to ensure your JSON fits these criteria.

## Key Criteria

At a high level, ingestion of JSON into Zep works best when these criteria are met:

1. **JSON is not too large**: Large JSON should be divided into pieces, adding each piece separately to Zep.
2. **JSON is not deeply nested**: Deeply nested JSON (more than 3 to 4 levels) should be flattened while preserving information.
3. **JSON is understandable in isolation**: The JSON should include all the information needed to understand the data it represents. This might mean adding descriptions or understandable attribute names where relevant.
4. **JSON represents a unified entity**: The JSON should ideally represent a unified entity, with ID, name, and description fields. Zep treats the JSON as a whole as a "first class entity", creating branching entities off of the main JSON entity from the JSON's attributes.

## JSON that is too large

### JSON with too many attributes

**Recommendation**: Split up the properties among several instances of the object. Each instance should duplicate the `id`, `name`, and `description` fields, or similar fields that tie each chunk to the same object, and then have 3 to 4 additional properties.

### JSON with too many list elements

**Recommendation**: Split up the list into its elements, ensuring you add additional fields to contextualize each element if needed. For instance, if the key of the list is "cars", then you should add a field which indicates that the list item is a car.

### JSON with large strings

**Recommendation**: A very long string might be better added to the graph as unstructured text instead of JSON. You may need to add a sentence or two to contextualize the unstructured text with respect to the rest of the JSON, since they would be added separately. And if it is very long, you would want to employ document chunking methods, such as described by Anthropic [here](https://www.anthropic.com/news/contextual-retrieval).

## JSON that is deeply nested

**Recommendation**: For each deeply nested value In the JSON, create a flattened JSON piece for that value specifically. For instance, if your JSON alternates between dictionaries and lists for 5 to 6 levels with a single value at the bottom, then the flattened version would have an attribute for the value, and an attribute to convey any information from each of the keys from the original JSON.

## JSON that is not understandable in isolation

**Recommendation**: Add descriptions or helpful/interpretable attribute names where relevant.

## JSON that is not a unified entity

**Recommendation**: Add an `id`, `name`, and `description` field to the JSON. Additionally, if the JSON essentially represents two or more objects, split it up.

## Dealing with a combination of the above

**Recommendation**: First, deal with the fact that the JSON is too large and/or too deeply nested by iteratively applying these recommendations (described above) from the top down: splitting up attributes, splitting up lists, flattening deeply nested JSON, splitting out any large text documents. For example, if your JSON has a lot of attributes and one of those attributes is a long list, then you should first split up the JSON by the attributes, and then split up the JSON piece that contains the long list by splitting the list elements.

After applying the iterative transformations, you should have a list of candidate JSON, each of which is not too large or too deeply nested. As the last step, you should ensure that each JSON in the list is understandable in isolation and represents a unified entity by applying the recommendations above.