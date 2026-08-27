> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Chunking Large Documents with Contextualized Retrieval

The `graph.add` endpoint has a 10,000 character limit per request. For larger documents, you need to chunk the content before ingestion. Simply splitting text can lose important context, so this cookbook demonstrates how to use **[contextualized retrieval](https://www.anthropic.com/news/contextual-retrieval)**—a technique where an LLM situates each chunk within the broader document before adding it to Zep.

This approach produces richer knowledge graphs with better entity and relationship extraction compared to naive chunking.

#### [Chunk documents with zep-ingest](/zep-ingest)

Use `ingest_documents` for paragraph-aware chunking, optional LLM context, preview, and submission.

View the complete source code on GitHub: [Python](https://github.com/getzep/zep/tree/main/examples/python/chunking-example) | [TypeScript](https://github.com/getzep/zep/tree/main/examples/typescript/chunking-example) | [Go](https://github.com/getzep/zep/tree/main/examples/go/chunking-example)

## Overview

The ingestion pipeline follows these steps:

1. **Read the document** from a text file
2. **Chunk the document** into smaller pieces using paragraph-aware splitting
3. **Contextualize each chunk** using an LLM to add situational context
4. **Add each chunk to Zep** via `graph.add`

## Setup

Install the required dependencies:

```bash Python
pip install zep-cloud openai python-dotenv
```

```bash TypeScript
npm install @getzep/zep-cloud openai dotenv
```

```bash Go
go get github.com/getzep/zep-go/v2 github.com/sashabaranov/go-openai github.com/joho/godotenv
```

Initialize the clients:

```python Python
import os
from openai import OpenAI
from zep_cloud.client import Zep
from dotenv import load_dotenv

load_dotenv()

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
zep_client = Zep(api_key=os.environ.get("ZEP_API_KEY"))
```

```typescript TypeScript
import { config } from "dotenv";
import { ZepClient } from "@getzep/zep-cloud";
import OpenAI from "openai";

config();

const openaiClient = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
const zepClient = new ZepClient({ apiKey: process.env.ZEP_API_KEY });
```

```go Go
import (
    "os"

    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    "github.com/getzep/zep-go/v2/option"
    "github.com/joho/godotenv"
    openai "github.com/sashabaranov/go-openai"
)

godotenv.Load()

openaiClient := openai.NewClient(os.Getenv("OPENAI_API_KEY"))
zepClient := zepclient.NewClient(option.WithAPIKey(os.Getenv("ZEP_API_KEY")))
```

## Chunking the Document

**Alternative chunking libraries:** If you prefer using an established library over the custom implementation below, consider [LangChain](https://docs.langchain.com/oss/python/integrations/splitters/index), [LlamaIndex](https://developers.llamaindex.ai/python/framework/module_guides/loading/node_parsers/), [Unstructured](https://docs.unstructured.io/open-source/core-functionality/chunking), or [Chonkie](https://docs.chonkie.ai).

The chunking algorithm splits text at paragraph boundaries first, then falls back to sentence boundaries for long paragraphs. This preserves semantic coherence better than fixed-size splitting.

```python Python
import re
from typing import Generator

def chunk_document(
    text: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50
) -> Generator[tuple[int, str], None, None]:
    """
    Split a document into chunks with configurable size and overlap.

    Args:
        text: The full document text
        chunk_size: Maximum characters per chunk (default 6000 to leave room for context)
        chunk_overlap: Characters to overlap between chunks for continuity

    Yields:
        Tuple of (chunk_index, chunk_text)
    """
    if not text:
        return

    text = text.strip()
    paragraphs = text.split('\n\n')

    current_chunk = ""
    chunk_index = 0

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        # If adding this paragraph exceeds chunk_size, yield current chunk
        if len(current_chunk) + len(paragraph) + 2 > chunk_size:
            if current_chunk:
                yield (chunk_index, current_chunk.strip())
                chunk_index += 1

                # Start new chunk with overlap from previous
                if chunk_overlap > 0 and len(current_chunk) > chunk_overlap:
                    overlap_text = current_chunk[-chunk_overlap:]
                    first_space = overlap_text.find(' ')
                    if first_space > 0:
                        overlap_text = overlap_text[first_space + 1:]
                    current_chunk = overlap_text + "\n\n"
                else:
                    current_chunk = ""

            # Handle single paragraphs longer than chunk_size
            if len(paragraph) > chunk_size:
                for sub_chunk in split_long_paragraph(paragraph, chunk_size, chunk_overlap):
                    yield (chunk_index, sub_chunk)
                    chunk_index += 1
                current_chunk = ""
            else:
                current_chunk += paragraph
        else:
            if current_chunk:
                current_chunk += "\n\n" + paragraph
            else:
                current_chunk = paragraph

    # Yield final chunk
    if current_chunk.strip():
        yield (chunk_index, current_chunk.strip())


def split_long_paragraph(
    paragraph: str,
    chunk_size: int,
    chunk_overlap: int
) -> Generator[str, None, None]:
    """Split a long paragraph by sentences."""
    sentences = re.split(r'(?<=[.!?])\s+', paragraph)
    current_chunk = ""

    for sentence in sentences:
        if len(current_chunk) + len(sentence) + 1 > chunk_size:
            if current_chunk:
                yield current_chunk.strip()
                if chunk_overlap > 0:
                    overlap = current_chunk[-chunk_overlap:]
                    first_space = overlap.find(' ')
                    if first_space > 0:
                        current_chunk = overlap[first_space + 1:] + " "
                    else:
                        current_chunk = ""
                else:
                    current_chunk = ""
        current_chunk += sentence + " "

    if current_chunk.strip():
        yield current_chunk.strip()
```

```typescript TypeScript
const CHUNK_SIZE = 500;
const CHUNK_OVERLAP = 50;

function splitIntoSentences(text: string): string[] {
  const sentences = text.match(/[^.!?]+[.!?]+[\s]*/g) || [text];
  return sentences.map((s) => s.trim()).filter((s) => s.length > 0);
}

function* chunkDocument(
  document: string,
  chunkSize: number = CHUNK_SIZE,
  chunkOverlap: number = CHUNK_OVERLAP
): Generator<[number, string]> {
  const paragraphs = document.split(/\n\n+/).filter((p) => p.trim().length > 0);

  let currentChunk = "";
  let chunkIndex = 0;

  for (const paragraph of paragraphs) {
    const trimmedParagraph = paragraph.trim();

    if (trimmedParagraph.length > chunkSize) {
      // Yield current chunk if it exists
      if (currentChunk.length > 0) {
        yield [chunkIndex, currentChunk.trim()];
        chunkIndex++;
        currentChunk = currentChunk.slice(-chunkOverlap);
      }

      // Split long paragraph by sentences
      const sentences = splitIntoSentences(trimmedParagraph);
      for (const sentence of sentences) {
        if (currentChunk.length + sentence.length + 1 > chunkSize) {
          if (currentChunk.length > 0) {
            yield [chunkIndex, currentChunk.trim()];
            chunkIndex++;
            currentChunk = currentChunk.slice(-chunkOverlap);
          }
        }
        currentChunk = currentChunk.length > 0
          ? currentChunk + " " + sentence
          : sentence;
      }
    } else {
      if (currentChunk.length + trimmedParagraph.length + 2 > chunkSize) {
        if (currentChunk.length > 0) {
          yield [chunkIndex, currentChunk.trim()];
          chunkIndex++;
          currentChunk = currentChunk.slice(-chunkOverlap);
        }
      }
      currentChunk = currentChunk.length > 0
        ? currentChunk + "\n\n" + trimmedParagraph
        : trimmedParagraph;
    }
  }

  if (currentChunk.trim().length > 0) {
    yield [chunkIndex, currentChunk.trim()];
  }
}
```

```go Go
import (
    "regexp"
    "strings"
)

const (
    ChunkSize    = 500
    ChunkOverlap = 50
)

func splitIntoSentences(text string) []string {
    re := regexp.MustCompile(`([.!?]+)\s+`)
    parts := re.Split(text, -1)
    delimiters := re.FindAllString(text, -1)

    var sentences []string
    for i, part := range parts {
        if part == "" {
            continue
        }
        sentence := part
        if i < len(delimiters) {
            sentence += strings.TrimSpace(delimiters[i])
        }
        sentences = append(sentences, sentence)
    }
    return sentences
}

func getOverlapText(text string, overlapSize int) string {
    if len(text) <= overlapSize {
        return text
    }
    overlap := text[len(text)-overlapSize:]
    spaceIdx := strings.Index(overlap, " ")
    if spaceIdx > 0 && spaceIdx < len(overlap)/2 {
        overlap = overlap[spaceIdx+1:]
    }
    return overlap
}

func chunkDocument(text string, chunkSize, chunkOverlap int) [][2]interface{} {
    paragraphs := regexp.MustCompile(`\n\s*\n`).Split(text, -1)

    var chunks [][2]interface{}
    var currentChunk strings.Builder
    chunkIndex := 0

    for _, para := range paragraphs {
        para = strings.TrimSpace(para)
        if para == "" {
            continue
        }

        if len(para) > chunkSize {
            // Yield current chunk if exists
            if currentChunk.Len() > 0 {
                chunks = append(chunks, [2]interface{}{chunkIndex, currentChunk.String()})
                chunkIndex++
                overlapText := getOverlapText(currentChunk.String(), chunkOverlap)
                currentChunk.Reset()
                currentChunk.WriteString(overlapText)
            }

            // Split long paragraph by sentences
            sentences := splitIntoSentences(para)
            for _, sentence := range sentences {
                sentence = strings.TrimSpace(sentence)
                if currentChunk.Len()+len(sentence)+1 > chunkSize && currentChunk.Len() > 0 {
                    chunks = append(chunks, [2]interface{}{chunkIndex, currentChunk.String()})
                    chunkIndex++
                    overlapText := getOverlapText(currentChunk.String(), chunkOverlap)
                    currentChunk.Reset()
                    currentChunk.WriteString(overlapText)
                }
                if currentChunk.Len() > 0 {
                    currentChunk.WriteString(" ")
                }
                currentChunk.WriteString(sentence)
            }
        } else {
            if currentChunk.Len()+len(para)+2 > chunkSize && currentChunk.Len() > 0 {
                chunks = append(chunks, [2]interface{}{chunkIndex, currentChunk.String()})
                chunkIndex++
                overlapText := getOverlapText(currentChunk.String(), chunkOverlap)
                currentChunk.Reset()
                currentChunk.WriteString(overlapText)
            }
            if currentChunk.Len() > 0 {
                currentChunk.WriteString("\n\n")
            }
            currentChunk.WriteString(para)
        }
    }

    if currentChunk.Len() > 0 {
        chunks = append(chunks, [2]interface{}{chunkIndex, currentChunk.String()})
    }

    return chunks
}
```

## Contextualizing Chunks

This is the key step that improves retrieval quality. For each chunk, we ask the LLM to generate a short context that situates it within the full document. This context is prepended to the chunk before adding to Zep.

**Cost optimization:** When contextualizing many chunks from the same document, use [prompt caching](https://platform.openai.com/docs/guides/prompt-caching) to cache the full document in the system prompt. This reduces inference time and cost since the document tokens are reused across chunk requests.

```python Python
def contextualize_chunk(
    openai_client: OpenAI,
    full_document: str,
    chunk: str
) -> str:
    """
    Use OpenAI to generate context for a chunk within its document.

    Args:
        openai_client: Initialized OpenAI client
        full_document: The complete document text
        chunk: The specific chunk to contextualize

    Returns:
        The contextualized chunk (context prepended to original chunk)
    """
    prompt = f"""<document>
{full_document}
</document>

Here is the chunk we want to situate within the whole document:
<chunk>
{chunk}
</chunk>

Please give a short succinct context to situate this chunk within the overall document for the purposes of improving search retrieval of the chunk. If the document has a publication date, please include the date in your context. Answer only with the succinct context and nothing else."""

    response = openai_client.chat.completions.create(
        model="gpt-5-mini-2025-08-07",
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=256
    )

    context = response.choices[0].message.content.strip()

    # Combine context with original chunk
    return f"{context}\n\n---\n\n{chunk}"
```

```typescript TypeScript
async function contextualizeChunk(
  openai: OpenAI,
  fullDocument: string,
  chunk: string
): Promise<string> {
  const prompt = `<document>
${fullDocument}
</document>

Here is the chunk we want to situate within the whole document:
<chunk>
${chunk}
</chunk>

Please give a short succinct context to situate this chunk within the overall document for the purposes of improving search retrieval of the chunk. If the document has a publication date, please include the date in your context. Answer only with the succinct context and nothing else.`;

  const response = await openai.chat.completions.create({
    model: "gpt-5-mini-2025-08-07",
    messages: [{ role: "user", content: prompt }],
    max_completion_tokens: 256,
  });

  const context = response.choices[0]?.message?.content?.trim() || "";

  // Combine context with original chunk
  return `${context}\n\n---\n\n${chunk}`;
}
```

```go Go
import (
    "context"
    "fmt"

    openai "github.com/sashabaranov/go-openai"
)

func contextualizeChunk(
    ctx context.Context,
    client *openai.Client,
    fullDocument string,
    chunk string,
) (string, error) {
    prompt := fmt.Sprintf(`<document>
%s
</document>

Here is the chunk we want to situate within the whole document:
<chunk>
%s
</chunk>

Please give a short succinct context to situate this chunk within the overall document for the purposes of improving search retrieval of the chunk. If the document has a publication date, please include the date in your context. Answer only with the succinct context and nothing else.`, fullDocument, chunk)

    resp, err := client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
        Model: "gpt-5-mini-2025-08-07",
        Messages: []openai.ChatCompletionMessage{
            {
                Role:    openai.ChatMessageRoleUser,
                Content: prompt,
            },
        },
        MaxCompletionTokens: 256,
    })

    if err != nil {
        return "", fmt.Errorf("OpenAI API error: %w", err)
    }

    contextText := resp.Choices[0].Message.Content

    // Combine context with original chunk
    return fmt.Sprintf("%s\n\n---\n\n%s", contextText, chunk), nil
}
```

## Adding Chunks to Zep

Each contextualized chunk is added to the user's graph using `graph.add`. Pass the same [`document_id`](/documents) on every chunk of one source so Zep groups them for extraction context and summarization. The method returns an episode object that can be used to track the ingestion.

```python Python
def add_chunk_to_zep(
    zep_client: Zep,
    user_id: str,
    chunk_data: str,
    document_id: str
) -> dict:
    """
    Add a contextualized chunk to Zep's graph.

    Args:
        zep_client: Initialized Zep client
        user_id: The user ID to add data to
        chunk_data: The contextualized chunk text
        document_id: Shared ID for every chunk of this source

    Returns:
        The episode response from Zep
    """
    episode = zep_client.graph.add(
        user_id=user_id,
        type="text",
        data=chunk_data,
        document_id=document_id
    )
    return episode
```

```typescript TypeScript
async function addChunkToZep(
  zepClient: ZepClient,
  userId: string,
  chunkData: string,
  documentId: string
): Promise<void> {
  await zepClient.graph.add({
    userId,
    type: "text",
    data: chunkData,
    documentId,
  });
}
```

```go Go
import (
    "context"

    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
)

func addChunkToZep(
    ctx context.Context,
    zepClient *zepclient.Client,
    userID string,
    chunkData string,
    documentID string,
) error {
    dataType := zep.GraphDataTypeText
    _, err := zepClient.Graph.Add(ctx, &zep.AddDataRequest{
        UserID:     zep.String(userID),
        Type:       &dataType,
        Data:       zep.String(chunkData),
        DocumentID: zep.String(documentID),
    })
    return err
}
```

## Complete Ingestion Pipeline

Here's how to put it all together:

```python Python
import os

def ingest_document(
    openai_client: OpenAI,
    zep_client: Zep,
    document_path: str,
    user_id: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50
) -> dict:
    """
    Ingest a document into Zep with contextualized retrieval.

    Args:
        openai_client: Initialized OpenAI client
        zep_client: Initialized Zep client
        document_path: Path to the text document
        user_id: Zep user ID to add the document to
        chunk_size: Maximum characters per chunk
        chunk_overlap: Character overlap between chunks

    Returns:
        Summary statistics of the ingestion
    """
    # Read document
    with open(document_path, 'r', encoding='utf-8') as f:
        full_document = f.read()

    document_id = os.path.splitext(os.path.basename(document_path))[0]

    # If document fits in a single request, add directly
    if len(full_document) <= 10000:
        episode = zep_client.graph.add(
            user_id=user_id,
            type="text",
            data=full_document,
            document_id=document_id,
        )
        return {"total_chunks": 1, "successful": 1, "episodes": [episode.uuid_]}

    # Chunk the document
    chunks = list(chunk_document(full_document, chunk_size, chunk_overlap))

    stats = {"total_chunks": len(chunks), "successful": 0, "episodes": []}

    for chunk_index, chunk_text in chunks:
        # Contextualize the chunk
        contextualized = contextualize_chunk(
            openai_client,
            full_document,
            chunk_text
        )

        # Validate size after contextualization
        if len(contextualized) > 10000:
            # Truncate context if needed
            excess = len(contextualized) - 10000
            contextualized = contextualized[excess:]

        # Add to Zep
        episode = add_chunk_to_zep(zep_client, user_id, contextualized, document_id)
        stats["successful"] += 1
        stats["episodes"].append(episode.uuid_)

    return stats
```

```typescript TypeScript
import * as fs from "fs";
import * as path from "path";

interface IngestStats {
  totalChunks: number;
  successful: number;
}

async function ingestDocument(
  openaiClient: OpenAI,
  zepClient: ZepClient,
  documentPath: string,
  userId: string,
  chunkSize: number = 500,
  chunkOverlap: number = 50
): Promise<IngestStats> {
  // Read document
  const fullDocument = fs.readFileSync(documentPath, "utf-8");
  const documentId = path.parse(documentPath).name;

  // If document fits in a single request, add directly
  if (fullDocument.length <= 10000) {
    await zepClient.graph.add({
      userId,
      type: "text",
      data: fullDocument,
      documentId,
    });
    return { totalChunks: 1, successful: 1 };
  }

  // Chunk the document
  const chunks = Array.from(chunkDocument(fullDocument, chunkSize, chunkOverlap));

  const stats: IngestStats = { totalChunks: chunks.length, successful: 0 };

  for (const [chunkIndex, chunkText] of chunks) {
    // Contextualize the chunk
    let contextualized = await contextualizeChunk(
      openaiClient,
      fullDocument,
      chunkText
    );

    // Validate size after contextualization
    if (contextualized.length > 10000) {
      // Truncate context if needed
      const excess = contextualized.length - 10000;
      contextualized = contextualized.slice(excess);
    }

    // Add to Zep
    await addChunkToZep(zepClient, userId, contextualized, documentId);
    stats.successful++;
  }

  return stats;
}
```

```go Go
import (
    "context"
    "fmt"
    "os"
    "path/filepath"
    "strings"

    "github.com/getzep/zep-go/v2"
    zepclient "github.com/getzep/zep-go/v2/client"
    openai "github.com/sashabaranov/go-openai"
)

type IngestStats struct {
    TotalChunks int
    Successful  int
}

func ingestDocument(
    ctx context.Context,
    openaiClient *openai.Client,
    zepClient *zepclient.Client,
    documentPath string,
    userID string,
    chunkSize int,
    chunkOverlap int,
) (*IngestStats, error) {
    // Read document
    docContent, err := os.ReadFile(documentPath)
    if err != nil {
        return nil, fmt.Errorf("error reading document: %w", err)
    }
    fullDocument := string(docContent)
    documentID := strings.TrimSuffix(filepath.Base(documentPath), filepath.Ext(documentPath))

    // If document fits in a single request, add directly
    if len(fullDocument) <= 10000 {
        dataType := zep.GraphDataTypeText
        _, err := zepClient.Graph.Add(ctx, &zep.AddDataRequest{
            UserID:     zep.String(userID),
            Type:       &dataType,
            Data:       zep.String(fullDocument),
            DocumentID: zep.String(documentID),
        })
        if err != nil {
            return nil, err
        }
        return &IngestStats{TotalChunks: 1, Successful: 1}, nil
    }

    // Chunk the document
    chunks := chunkDocument(fullDocument, chunkSize, chunkOverlap)

    stats := &IngestStats{TotalChunks: len(chunks), Successful: 0}

    for _, chunk := range chunks {
        chunkText := chunk[1].(string)

        // Contextualize the chunk
        contextualized, err := contextualizeChunk(ctx, openaiClient, fullDocument, chunkText)
        if err != nil {
            continue
        }

        // Validate size after contextualization
        if len(contextualized) > 10000 {
            // Truncate context if needed
            excess := len(contextualized) - 10000
            contextualized = contextualized[excess:]
        }

        // Add to Zep
        if err := addChunkToZep(ctx, zepClient, userID, contextualized, documentID); err != nil {
            continue
        }
        stats.Successful++
    }

    return stats, nil
}
```

## Usage Example

```python Python
# Ensure the user exists
user_id = "user123"
zep_client.user.add(user_id=user_id)

# Ingest a document
stats = ingest_document(
    openai_client=openai_client,
    zep_client=zep_client,
    document_path="company_handbook.txt",
    user_id=user_id,
    chunk_size=500,
    chunk_overlap=50
)

print(f"Ingested {stats['successful']} of {stats['total_chunks']} chunks")
```

```typescript TypeScript
// Ensure the user exists
const userId = "user123";
await zepClient.user.add({ userId });

// Ingest a document
const stats = await ingestDocument(
  openaiClient,
  zepClient,
  "company_handbook.txt",
  userId,
  500,
  50
);

console.log(`Ingested ${stats.successful} of ${stats.totalChunks} chunks`);
```

```go Go
// Ensure the user exists
userID := "user123"
zepClient.User.Add(ctx, &zep.CreateUserRequest{
    UserID: zep.String(userID),
})

// Ingest a document
stats, err := ingestDocument(
    ctx,
    openaiClient,
    zepClient,
    "company_handbook.txt",
    userID,
    500,
    50,
)
if err != nil {
    log.Fatalf("Failed to ingest document: %v", err)
}

fmt.Printf("Ingested %d of %d chunks\n", stats.Successful, stats.TotalChunks)
```

## Best practices

* **Chunk size**: Use 500 characters or less for optimal graph construction. Smaller chunks allow Zep to capture more granular entities and relationships.
* **Chunk overlap**: 50 characters helps maintain continuity between chunks without excessive redundancy.
* **Small chunks produce better graphs**: Zep can capture more entities and relationships from smaller, focused chunks. While the 10K character limit allows larger chunks, smaller chunks yield richer knowledge graphs.

## Further Reading

* [Documents](/documents) - Group chunks of the same source with `document_id`
* [Adding Business Data](/adding-business-data) - Learn about the `graph.add` endpoint and data types
* [Batch ingestion](/adding-batch-data) - For ingesting large historical datasets in a single batch
* [Performance Best Practices](/performance-best-practices) - Optimization tips for data ingestion