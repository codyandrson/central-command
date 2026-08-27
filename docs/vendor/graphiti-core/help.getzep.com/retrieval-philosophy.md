> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Retrieval philosophy

> Zep optimizes retrieval for high recall and low latency — sub-200ms Context Graph retrieval that gives agents complete context for reliable decisions.

Zep's retrieval system is designed with two primary goals: **high recall** and **low latency**. This is a deliberate architectural choice that differs from systems optimized for precision.

## Understanding recall vs. precision

Think of recall and precision as two different ways to measure retrieval quality:

* **Recall** measures completeness: "Did we find all the relevant information?"
* **Precision** measures accuracy: "Is everything we returned actually relevant?"

In practical terms:

* **High recall** means you get all the relevant results, but might also get some less relevant ones
* **High precision** means everything returned is highly relevant, but you might miss important information

## The tradeoff in practice

| Approach                                 | What You Get                                        | What You Risk                                        | Best For                                                                        |
| ---------------------------------------- | --------------------------------------------------- | ---------------------------------------------------- | ------------------------------------------------------------------------------- |
| **Optimize for Recall** (Zep's approach) | All relevant facts, plus some less relevant results | Larger context with some noise                       | Agents that need complete information to make decisions; real-time applications |
| **Optimize for Precision**               | Only highly relevant results                        | Missing critical facts that could cause task failure | Use cases where context size is severely constrained; manual review workflows   |

### Example scenario

User query: *"What did we discuss about the Q2 marketing budget?"*

| Retrieval Approach         | Results Returned                                                                                                                                             | Outcome                                                                                                                                                                             |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Recall-Optimized** (Zep) | • Q2 marketing budget discussion ✓<br />• Related Q2 sales projections ✓<br />• Q3 budget planning mention ⚠️<br />• Q2 hiring costs mentioning marketing ⚠️ | Agent has complete context, including tangentially related information. Can successfully answer follow-up questions about budget revisions.                                         |
| **Precision-Optimized**    | • Q2 marketing budget discussion ✓<br />• Related Q2 sales projections ✓                                                                                     | Clean, focused results, but **missing** a separate conversation about budget revisions that didn't explicitly mention "marketing budget." Agent may provide incomplete information. |

## Why recall over precision?

Agents need comprehensive context to make informed decisions. Missing a critical fact can cause an agent to fail its task or provide incorrect information. By optimizing for recall, Zep ensures that relevant information is available to the agent, even if that means returning more results than strictly necessary.

The underlying principle: it's better to provide complete information and let the agent or downstream LLM filter what's relevant than to risk omitting something important.

## Why latency matters

Real-time applications like conversational AI, live customer support, and interactive agents require fast responses. Zep's retrieval architecture is optimized to return results in under 200ms regardless of Context Graph size or count, with no perceptible delay for the user.

Public benchmarks validate this recall-optimized, low-latency design. On [LoCoMo](https://arxiv.org/abs/2402.17753), Zep records 94.7% accuracy at 155ms retrieval latency; on [LongMemEval](https://arxiv.org/abs/2410.10813), 90.2% accuracy at 162ms. Zep achieves high recall and low latency together rather than trading one for the other.

## Tuning the recall-precision tradeoff

The recall-optimized approach described here is how Zep is tuned **out of the box**. However, Zep provides several mechanisms to adjust this tradeoff for different use cases:

* **Limit search results**: Control the maximum number of results returned
* **Apply filters**: Narrow retrieval to specific time ranges, Entity and/or Edge labels, or other criteria
* **Adjust search parameters**: Fine-tune ranking and relevance thresholds

These controls allow you to shift toward precision when your application demands it, while maintaining Zep's fast retrieval performance.

## Balancing context size

While recall is our priority, Zep does consider token count when returning results. We balance the size of the resulting context with the goal of providing complete information, but when in doubt, we err on the side of ensuring your agent has what it needs to succeed.