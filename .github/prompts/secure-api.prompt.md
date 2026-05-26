---
name: Secure API Review
description: Review and harden selected Python backend code against common API security vulnerabilities, following OWASP guidelines and project conventions.
mode: edit
---

You are an expert in Python backend security and API hardening.

Analyze the selected code and fix all security issues found, aligned with the **OWASP Top 10**.

## Areas to Review

### Secrets & Credentials
- Tokens (`SONAR_TOKEN`, `OPENAI_API_KEY`, `AZURE_CLIENT_SECRET`, etc.) must only be read from env vars — never hardcoded or logged
- Validate that required env vars are present **before** making API calls; raise a clear error if missing
- Redact any sensitive values from log output and exception messages

### Input Validation
- Validate and sanitize all external inputs at system boundaries (HTTP responses, JSON payloads, env vars)
- Reject or safely handle unexpected types, missing keys, and out-of-range values
- Do not trust API responses blindly — validate shape before accessing nested fields

### Error Handling & Information Leakage
- Exception messages must not reveal secrets, internal paths, or stack traces to external callers
- Use generic error messages for external-facing responses; log full details internally only

### External HTTP Requests
- All outbound requests (`requests`, `httpx`) must define explicit **timeouts**
- Retry logic must include **exponential backoff** with a maximum retry count
- Verify TLS certificates — do not set `verify=False`
- Validate HTTP response status codes before consuming the response body

### Logging
- Never log credential values, tokens, or personally identifiable information
- Use structured log levels (`logging.error`, `logging.warning`) consistently

## Project-Specific Integrations to Protect

- **SonarCloud** — token in headers, validate project/org keys
- **OpenAI** — API key in Authorization header, validate model name
- **Microsoft Graph** — client secret, tenant ID, access token lifecycle

## Project Constraints

- Do **not** add new external libraries
- Handle errors only at system boundaries (do not wrap every line in try/except)
- Preserve the current module structure

## Output

1. **Corrected code** — with all security fixes applied
2. **Risk summary** — a brief bullet list (max 5 items) of vulnerabilities found and how each was fixed