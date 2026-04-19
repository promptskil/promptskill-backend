# Path B — Multi-Provider Generation Architecture

## Decision

Replace single-provider pipeline (Claude generates all prompts) with multi-provider pipeline (each AI generates its own prompts). ChatGPT selection calls OpenAI, Gemini calls Google, Grok calls xAI, Claude stays on Anthropic.

## Why

Claude imitating ChatGPT's style != ChatGPT reasoning through the topic itself. When a student pastes a prompt into ChatGPT, a prompt written BY ChatGPT lands naturally because it was reasoned in ChatGPT's native pattern. Same for Gemini, Grok, Claude. The prompt quality jump is the product differentiation.

## Spec Amendment

This document amends Layer 7 of the full-stack-engineer specification. Layer 7 was locked as "Anthropic API Integration" (single provider). This amendment expands it to "Multi-Provider Generation Integration" (four providers). All other layers remain unchanged.

Version: Layer 7 v2
Approved by: Jeremie (2026-04-18)
Type: Option C — Scoped Path B. Layer 7 expanded, client/DB/API shape unchanged.

---

## Diagnosis Findings (7 items traced, 4 require fixes)

### Finding 1 — Layer 7 scope expansion (Type P)
Layer 7 locked as Anthropic-only. Path B adds OpenAI, Google, xAI.
**Fix:** This document IS the spec amendment. Version as Layer 7 v2.

### Finding 2 — Celery sync/async boundary
Spec locks: "Do NOT use async/await in Celery tasks." Path B ModelClient must support both.
**Fix:** ModelClient ABC exposes two methods: `async agenerate()` for FastAPI, `generate()` for Celery. Each adapter implements both.

### Finding 3 — Config shape rename (clean)
`anthropic_model_id` renamed to `provider` + `provider_model_id`. All references are in backend files being modified. No client impact.
**Fix:** Already in execution order. No gap.

### Finding 4 — User message template needs per-provider tuning
"Generate an optimized chatgpt prompt" is awkward when ChatGPT itself receives it.
**Fix:** Add `user_message_template` to JSON config. Each provider gets a natural framing.

### Finding 5 — Google GenAI exception classes need validation
`google-genai` SDK uses different exceptions than `google.api_core`.
**Fix:** Validate exact classes from SDK docs at implementation time. Mapping principle unchanged.

### Finding 6 — Cost tracking gap (non-blocking)
Multi-provider = multi-billing. No cost monitoring in spec.
**Fix:** Track as future ops item. Not needed for v2 launch.

### Finding 7 — System prompts must shift to second-person
Current: "generate a prompt FOR ChatGPT" (third-person, Claude writing for another AI).
Needed: "generate the best prompt leveraging YOUR strengths" (second-person, native AI writing for itself).
**Fix:** Rewrite all 4 system prompts for native provider context.

---

## Architecture: Output Backward to Input

### Output (what the student receives — unchanged)

```
{ prompt_id: UUID, prompt: string }
```

Response shape does NOT change. Client code untouched. Database schema untouched. The `system_prompt_version` field already tracks which path generated the prompt (v1 = Claude proxy, v2 = native provider).

### Structure (how the pipeline is organized)

```
generate_service.py
  +-- generate_prompt(model, topic, ...)
        |-- dispatch to ModelClient by config["provider"]
        |     |-- AnthropicClient   (claude)
        |     |-- OpenAIClient      (chatgpt)
        |     |-- GeminiClient      (gemini)
        |     +-- GrokClient        (grok — OpenAI-compatible API)
        |-- timeout wrapper (30s global ceiling)
        |-- error handling -> fallback template (unchanged)
        +-- database INSERT (unchanged)
```

### Requirements (what changes)

1. **New file: `app/services/model_clients.py`**
   - Abstract base: `ModelClient`
     - `async agenerate(system_prompt, user_message, model_id, max_tokens) -> str` (FastAPI)
     - `generate(system_prompt, user_message, model_id, max_tokens) -> str` (Celery)
   - Four implementations: `AnthropicClient`, `OpenAIClient`, `GeminiClient`, `GrokClient`
   - `GrokClient` extends `OpenAIClient` with different `base_url`
   - Each normalizes provider response to plain string
   - Each maps provider exceptions to common types: `ProviderRateLimitError`, `ProviderAPIError`

2. **Updated file: `app/services/generate_service.py`**
   - Replace direct `anthropic.AsyncAnthropic` call with `get_client(config["provider"]).agenerate(...)`
   - Build user message from config `user_message_template` (new field)
   - Catch `ProviderRateLimitError` -> Celery retry (replaces `anthropic.RateLimitError`)
   - Catch `ProviderAPIError` -> fallback template (replaces `anthropic.APIStatusError`)

3. **Updated file: `app/tasks/generate_task.py`**
   - Replace direct `anthropic.Anthropic` call with `get_client(config["provider"]).generate(...)`
   - Same exception mapping as generate_service
   - Sync client only (spec requirement)

4. **Updated files: `app/prompts/{model}.json`**
   - Replace `anthropic_model_id` with `provider` + `provider_model_id`
   - Add `user_message_template` field
   - Rewrite `system_prompt` for native provider context (second-person)
   - Bump version from `v1` to `v2`

5. **Updated file: `requirements.txt`**
   - Add: `openai`, `google-genai`
   - xAI reuses `openai` SDK with different base_url — no extra dependency

6. **Updated file: `app/config.py`**
   - Add: `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `XAI_API_KEY` env vars

7. **Updated file: `.env` / Railway env vars**
   - Add three new API keys

### Data Flow (how a request moves through the system)

```
Client POST /generate { model: "chatgpt", topic: "..." }
  -> Router (unchanged)
    -> generate_service.generate_prompt()
      -> Load config from MODEL_REGISTRY["chatgpt"]
      -> config["provider"] = "openai"
      -> get_client("openai") -> OpenAIClient
      -> Build user_message from config["user_message_template"].format(topic=topic)
      -> OpenAIClient.agenerate(
            system_prompt=config["system_prompt"],
            user_message=user_message,
            model_id=config["provider_model_id"],  # "gpt-4o"
            max_tokens=1000
        )
      -> OpenAI API call with 30s timeout
      -> Returns prompt text string
      -> Database INSERT (unchanged, version="v2")
      -> Return { prompt_id, prompt }
```

### Input (what feeds the system — new API keys)

| Provider | SDK | API Key Env Var | Model ID | Base URL |
|----------|-----|----------------|----------|----------|
| Anthropic | `anthropic` (existing) | `ANTHROPIC_API_KEY` (existing) | `claude-sonnet-4-6` | default |
| OpenAI | `openai` (new) | `OPENAI_API_KEY` (new) | `gpt-4o` | default |
| Google | `google-genai` (new) | `GOOGLE_API_KEY` (new) | `gemini-2.0-flash` | default |
| xAI | `openai` (reuse) | `XAI_API_KEY` (new) | `grok-3` | `https://api.x.ai/v1` |

---

## Config Shape Change

### Before (v1 — Claude proxy)
```json
{
  "version": "v1",
  "model": "chatgpt",
  "anthropic_model_id": "gpt-4o",
  "system_prompt": "You are a prompt optimization expert. The user will give you a topic. Generate a single, highly optimized prompt for ChatGPT that leverages ChatGPT's strength in structure-driven output..."
}
```

### After (v2 — native provider)
```json
{
  "version": "v2",
  "model": "chatgpt",
  "provider": "openai",
  "provider_model_id": "gpt-4o",
  "user_message_template": "Generate the best possible prompt about: '{topic}'",
  "system_prompt": "You are a prompt optimization expert. When given a topic, generate a single highly optimized prompt that leverages your strengths in structured, organized output. Structure the prompt to: declare the desired output format first, then the sections the response must contain, then one or two concrete examples. Respond with only the prompt text. No explanation. No preamble."
}
```

Key changes:
- `anthropic_model_id` -> `provider` + `provider_model_id`
- Added `user_message_template` with `{topic}` placeholder
- System prompt rewritten from third-person ("for ChatGPT") to second-person ("your strengths")
- Version bumped to `v2` for analytics separation

---

## Error Handling Map

| Common Exception | Provider Source | Action |
|---|---|---|
| `ProviderRateLimitError` | `anthropic.RateLimitError`, `openai.RateLimitError`, Google rate limit | Celery retry |
| `ProviderAPIError` | `anthropic.APIStatusError`, `openai.APIStatusError`, Google API error | Fallback template |
| `asyncio.TimeoutError` | All providers (30s ceiling) | 504 HTTPException |

model_clients.py catches provider-specific exceptions and raises common types. generate_service.py only catches common types. This keeps the service layer provider-agnostic.

---

## Execution Order

1. Get API keys: OpenAI, Google AI, xAI accounts
2. Add API keys to `app/config.py` and `.env`
3. Install new SDKs: `pip install openai google-genai`
4. Create `app/services/model_clients.py` — ModelClient ABC + 4 adapters (async + sync)
5. Update JSON configs to v2 shape (provider, provider_model_id, user_message_template, rewritten system_prompt)
6. Update `load_model_registry()` validation for new required keys
7. Update `generate_service.py` — dispatch via get_client(), catch common exceptions
8. Update `generate_task.py` — same dispatch, sync client only
9. Test each provider independently (unit test per adapter)
10. Deploy to Railway with new env vars
11. Monitor v2 generations via system_prompt_version field

---

## What Does NOT Change

- Client app (React Native) — zero changes
- Database schema — zero changes
- Response shape — zero changes
- Router (`app/routers/generate.py`) — zero changes
- Fallback templates (`app/prompts/fallback/`) — zero changes
- Rate limiting — zero changes
- Auth — zero changes

---

## Future Items (not in v2 scope)

- Per-provider cost tracking and alerting
- A/B testing v1 (Claude proxy) vs v2 (native) via system_prompt_version analytics
- Provider health monitoring and automatic fallback to Claude proxy if native provider is down
