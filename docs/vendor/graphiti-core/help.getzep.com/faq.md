> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# FAQ

> Answers about Zep agent memory: multi-tenancy and isolation, scaling to millions of users, self-hosting and BYOC, Graphiti, sub-200ms retrieval, pricing, and billing.

## Is user data isolated from other users? How does multi-tenancy work?

Yes, Zep provides full multi-tenant isolation. All graphs—whether user graphs or standalone graphs—are completely isolated from each other with no shared state.

* **User graphs**: When you call `thread.get_user_context()`, it retrieves context only from that user's graph. Other users' data is never accessible.
* **Standalone graphs**: Each standalone graph is independent and isolated from other graphs.
* **Access management**: You control which graphs your application accesses by specifying `user_id` or `graph_id` in API calls.

If you need to share context across users, you can create a standalone graph and query it explicitly. See our [cookbook on sharing context across users](/cookbook/share-context-across-users-using-graphs).

## How well does Zep scale?

Zep supports many millions of users per account and retrieval performance is not impacted by dataset size. Retrieving/searching the graph scales in near constant time with the size of the graph. Zep's Metered Billing Plan is subject to rate limits on both API requests and processing concurrency.

## Can I self host Zep? What happened to Zep Community Edition?

Zep Community Edition, which allows you to host Zep locally, is deprecated and no longer supported. See our [announcement post here](https://blog.getzep.com/announcing-a-new-direction-for-zeps-open-source-strategy/).

The alternatives we offer include:

* [Zep Cloud](https://www.getzep.com/): Our hosted solution
* [Graphiti](https://github.com/getzep/graphiti): The open-source Context Graph framework that powers Zep Cloud
* **BYOC (Bring Your Own Cloud)**: For enterprise customers who need VPC residency and maximum control, we offer BYOC deployments where Zep runs in your own cloud infrastructure. [Contact our Enterprise team](https://www.getzep.com/enterprise) to learn more.

## Is there a limit on the number of graphs I can create?

No, there is no limit on the number of graphs you can create.

## Is there a limit on the size of the graph?

No, there is no limit on the size of the graph.

## What's the difference between Zep and Graphiti?

See our detailed comparison: [Zep vs Graphiti](/zep-vs-graphiti)

## Does Zep Cloud support multiple spoken languages?

We have official multilingual support on our roadmap, enabling the creation of graphs in a user's own language. Currently, graphs are not explicitly created in the user's language. However, Zep should work well today with any language, provided you're using a multilingual LLM and your own prompts explicitly state that responses to the user should be in their language.

## Is there a free version of Zep Cloud?

Yes - Zep offers a free tier. See [Pricing](https://www.getzep.com/pricing) for more information.

## How do I access previous billing statements?

Open the [billing page](https://app.getzep.com/account/billing) in the Zep dashboard. On a paid plan, click **Open Billing Portal** to view and download invoices.

For Enterprise invoices, contact [support@getzep.com](mailto:support@getzep.com).

## How do I update billing information such as a tax ID or VAT number?

Use the billing portal to update your company name, billing address, and tax ID or VAT number.

1. Open the [billing page](https://app.getzep.com/account/billing) in the Zep dashboard.
2. Click **Open Billing Portal**.
3. Update the billing details in the portal.

For Enterprise billing details, contact [support@getzep.com](mailto:support@getzep.com).

## Can I use Zep to replace RAG over static documents?

Zep can be used for retrieval for static documents just like RAG or GraphRAG, although this is not what Zep was designed for. Zep was designed for dynamic, changing data, which RAG and GraphRAG were not designed to do.

## How does the retrieval work for thread.get\_user\_context under the hood?

`thread.get_user_context` does a `graph.search` on nodes, edges, and episodes using the [MMR reranker](/searching-the-graph#mmr-maximal-marginal-relevance). It uses the most recent message as the search query. In addition, it does a `BFS` on the 4 most recent episodes (so it finds all nodes, edges, and episodes created by the 4 most recent episodes and all nodes and edges 2 connections deep).

All of those search results are then used as candidate results which are reranked by the [MMR reranker](/searching-the-graph#mmr-maximal-marginal-relevance). The MMR reranker will compare each search result with the most recent 4 messages to determine how relevant that result is to the current conversation.

## What is the API URL for Zep Cloud?

The API URL for Zep Cloud is `https://api.getzep.com`. Note that you do not need to specify the API URL when using the Cloud SDKs.
If a service requests the Zep URL, it is possible it's only compatible with the Zep Community Edition service.

## Should I use nodes, edges, or episodes when searching the graph and creating a context string?

You can use any combination of nodes, edges, and episodes. There is not a one size fits all solution, and you will likely need to experiment with different approaches to get the best performance for your use case.

## How do I add messages or manage context for a group chat with multiple people?

In order to add messages for a group chat to a Zep Context Graph, you need to use the `graph.add` method with `type = message` as opposed to `thread.add_messages` (which uses `graph.add` with `type = message` under the hood). You need to use the `graph.add` method so that you are not associating the chat with a single user. Then, to retrieve context, you need to search the graph and assemble a custom context block ([see cookbook example](/cookbook/advanced-context-block-construction)).

## Does Zep handle emojis? Should I convert them to unicode characters before adding them to Zep?

Yes, Zep can handle emojis directly. Send the emojis as-is in unicode strings—there's no need to encode them. In fact, it's preferable that they remain unencoded.

## I am seeing information duplicated between different node summaries. Is this normal?

This is a normal and intended feature of Zep. Node summaries are intended to be standalone summaries of the node, which often means describing the relationships that that node has to other nodes. Those same relationships are likely to appear in the summaries of those other nodes.

## Why aren't my episodes processing?

Sometimes episodes may appear to not be processing when they are actually processing slowly. Typically, episodes process in less than 10 seconds, but occasionally they can take a few minutes. Additionally, if you add multiple episodes to a single graph simultaneously, they must process sequentially, which can take time if there are many episodes.

Please confirm the following:

* **Have you exhausted your episode credits?** If you're on the free tier, you have a limited number of episode credits per month. Once depleted, episodes will stop processing until the next billing cycle or until you upgrade to a paid plan. Check your usage in the [Zep dashboard](https://app.getzep.com) and see our [Pricing page](https://www.getzep.com/pricing) for plan options.
* Are you adding multiple episodes to a single graph all at once? If so, how many? Multiply the number of episodes you are adding to a single graph by 10 seconds for an average case time estimate, or by a few minutes for a worst case time estimate.
* If the above is the case, within the web app, find the most recently processed episode and then look at the next unprocessed episode. Confirm whether that episode remains unprocessed after waiting at least 3-4 minutes (the worst-case processing time). If you see this episode process after some waiting, then your episodes are processing, it just may take some time.
* If neither of the above applies, reach out to our support team at [support@getzep.com](mailto:support@getzep.com) and let them know what you are seeing.

## How do I get the playground to work with my own data?

The playground is not meant to work with custom data. Instead the playground showcases Zep's functionality with demo data. In order to create a graph with your own custom data, you need to use the Zep SDKs. See our [Quickstart](/quickstart).

## I can't join my company project, because I have already created an account. What should I do?

You will need to delete your account and then accept the invitation from your company.

## How do I get Zep to work with n8n?

The Zep n8n integration is no longer supported. We recommend using Zep's SDKs directly instead, see [here](https://help.getzep.com/quickstart).