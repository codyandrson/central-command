> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# Bring Your Own LLM (BYOM)

Enterprise Add-on. Contact [sales](mailto:sales@getzep.com) to enable BYOM for your account.

## Overview

Bring Your Own LLM (BYOM) lets you use your own accounts with model providers such as OpenAI, Anthropic, and Google when using Zep Cloud. You keep using Zep's agent memory, context assembly, and governance controls, routing inference through credentials you manage. This approach ensures:

* **Contract continuity:** Apply your negotiated pricing, quotas, and compliance commitments with each LLM vendor.
* **Data governance:** Enforce provider-specific policies for data usage, retention, and residency.
* **Operational flexibility:** Configure the best vendor or model for each project, including fallbacks for high availability.

## Model recommendations

Zep intentionally sets thinking and reasoning budgets off or low to minimize cost and latency. We recommend using smaller, faster models optimized for speed rather than extended reasoning.

**Recommended model:** Gemini 2.5 Flash Lite is the most well-tested model with Zep.

Not all larger models support disabling reasoning entirely. If you configure a model that requires reasoning tokens, you may experience higher costs and latency. Smaller models avoid this issue.

## Supported providers

Zep only uses text generation endpoints—no embeddings, fine-tuning, file uploads, or assistants. LLM providers are configured at the **account level**, meaning the same credentials are used for all projects within your account.

| Provider                    | Credentials          | Required permissions                            | Additional configuration                                                                                      |
| --------------------------- | -------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| **OpenAI**                  | API key              | Chat completions, Responses                     | Organization ID (optional), Base URL (optional; OpenAI regional hosts such as `https://eu.api.openai.com/v1`) |
| **Azure OpenAI**            | API key              | None (key provides access)                      | Endpoint URL, API version (optional)                                                                          |
| **Google Gemini**           | API key              | None (full access by default)                   | —                                                                                                             |
| **Google Vertex AI**        | Service account JSON | Vertex AI User (`roles/aiplatform.user`)        | GCP project, location (optional)                                                                              |
| **Anthropic**               | API key              | None (full access by default)                   | —                                                                                                             |
| **AWS Bedrock (Anthropic)** | IAM role ARN         | Bedrock model access (cross-account AssumeRole) | AWS region, external ID (optional)                                                                            |

## Getting started

### Navigate to LLM Providers

In the Zep dashboard, go to **Settings ▸ LLM Providers**.

### Add a provider

Select a provider type from the dropdown and enter your credentials. For providers requiring JSON credentials (Vertex AI, Bedrock), paste the full JSON object.

### Configure provider settings

Enter any provider-specific settings such as endpoint URLs, project IDs, or regions.

### Select a model

Choose a model from the list of verified models for your provider. Mark it as primary or fallback.

### Set rate limits

Configure **TPM Capacity** and **TPM Refill/s** to control token usage. Optionally add **Labels** for cost allocation.

### Save and verify

Click **Save & Verify** to validate your credentials. Zep makes a test API call to confirm authentication.

## Configuration options

When configuring a provider, you can set the following options:

| Option           | Description                                                                     | Default |
| ---------------- | ------------------------------------------------------------------------------- | ------- |
| **TPM Capacity** | Maximum tokens per minute allowed. This is your rate limit bucket size.         | 90,000  |
| **TPM Refill/s** | Tokens added to your rate limit bucket per second. Controls replenishment rate. | 1,500   |
| **Labels**       | Key-value tags passed to the LLM provider for cost allocation and tracking.     | —       |
| **Primary**      | Designate this provider/model as the default for inference requests.            | —       |
| **Fallback**     | Use this provider/model when the primary is unavailable or rate limited.        | —       |

## Provider-specific notes

### Azure OpenAI

**Deployment name must match model ID**

Your Azure deployment name must match the model ID exactly. For example, if you're using `gpt-4.1`, your deployment name must be `gpt-4.1`—not a custom name like `my-gpt-deployment`. A mismatched deployment name causes "model or resource not found" errors.

**Use the base endpoint URL**

Use the base **Azure Endpoint** URL, not the Target URI from the deployment page. Using the Target URI causes a "model or resource not found" error.

**Correct:** `https://your-resource-name.openai.azure.com/`

**Incorrect:** `https://your-resource-name.openai.azure.com/openai/deployments/.../chat/completions?api-version=...`

Find the correct endpoint in the Azure portal under **Keys and Endpoint**, or in the `azure_endpoint` value shown in the Python code examples on the deployment page.

### Google Vertex AI

Vertex AI uses service account authentication, which differs from API key authentication used by Google Gemini (AI Studio). You'll need to gather three pieces of information from the Google Cloud Console:

**Project ID**

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Select your project from the project picker
3. Copy the project ID from the URL query string: `?project=your-project-id`

**Service Account JSON**

1. In the Google Cloud Console, search for "Service Accounts" or navigate to **IAM & Admin → Service Accounts**
2. Click **Create Service Account**
3. Give it a name and grant the **Vertex AI User** role (`roles/aiplatform.user`)—this is the only role required
4. Once created, click on the service account to open its details
5. Go to the **Keys** tab and click **Add Key → Create new key → JSON**
6. A JSON file will download—paste its entire contents into the **Service Account JSON** field in Zep

**Location**

Enter your preferred GCP region (e.g., `us-central1`). If omitted, Zep uses a default region.

We recommend using Vertex AI over Google Gemini (AI Studio) for production workloads. Vertex AI offers better control over rate limits, allows you to increase quotas, and supports purchasing provisioned throughput if needed.

## FAQ

**Does Zep store our provider keys in its databases?**
No. Credentials are stored in an encrypted secrets manager (AWS SSM Parameter Store). Values are decrypted in memory only when needed and are never written to Zep databases or logs.

**Can we use different vendors or models per project?**
Yes. Each project maintains its own provider configuration, including defaults and fallbacks. This is useful for isolating production from staging or testing providers side by side.

**Can we prevent vendors from training on our data?**
Yes. Use the vendor endpoints and contractual controls that disable data retention or training. Zep routes requests accordingly and sets the necessary flags in each call.

**How is usage billed?**
You receive invoices from Zep for Zep services only. LLM inference charges come directly from your vendors under your existing contract and pricing.

**What happens if a key is compromised or needs rotation?**
Add a new credential in the dashboard and verify it. Then disable or delete the previous credential. Requests start using the new credential immediately with no downtime required.

**How does BYOM affect observability?**
Requests are tagged by project and provider, so you can attribute usage and costs. Rate limits are applied per provider to protect budgets and enforce quotas.

**Can we use a customer-managed KMS key?**
Contact support if you require customer-controlled encryption for credential storage.