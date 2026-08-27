> For clean Markdown of any page, append .md to the page URL.
> For a complete documentation index, see https://help.getzep.com/llms.txt.
> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server at https://help.getzep.com/_mcp/server.

# HIPAA compliance

> Guidelines for using Zep in HIPAA-compliant applications

When building healthcare applications that handle protected health information (PHI), you must ensure that identifiers used within Zep do not expose PHI.

Zep offers Business Associate Agreements (BAAs) for Enterprise customers. [Contact our Enterprise team](https://www.getzep.com/enterprise) to learn more about HIPAA-compliant deployments.

## Identifier requirements

To maintain HIPAA compliance when using Zep, user IDs, thread IDs, and graph IDs must not contain personally identifiable information (PII). Identifiers appear in logs, error messages, and analytics data, so embedding PII in them risks inadvertent exposure.

| Identifier type | Requirement                                                                                          |
| --------------- | ---------------------------------------------------------------------------------------------------- |
| User ID         | Use UUIDs or internal system identifiers. Do not use email addresses, names, or patient identifiers. |
| Thread ID       | Use UUIDs. Do not embed user information or session details that could identify a person.            |
| Graph ID        | Use UUIDs or descriptive names that do not contain PII.                                              |

```python
import uuid

# Correct: opaque identifier with no PII
user_id = str(uuid.uuid4())  # e.g., "550e8400-e29b-41d4-a716-446655440000"

# Incorrect: contains PII
user_id = "john.doe@hospital.com"  # Contains email
user_id = "patient-12345"  # Contains medical record number
```

## Mapping identifiers

Maintain a secure mapping between opaque Zep identifiers and internal user records in your own database:

```python
# Your internal patient record links to the opaque Zep user ID
patient_record = {
    "internal_patient_id": "MRN-12345",
    "name": "Jane Doe",
    "zep_user_id": "550e8400-e29b-41d4-a716-446655440000"
}

# Use the opaque ID when interacting with Zep
zep_client.user.add(user_id=patient_record["zep_user_id"])
```