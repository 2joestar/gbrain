# model-discovery — Auto-Discover & Rank Models

**Trigger:** `phoenix models refresh` | `phoenix models for "<intent>"` | weekly Sunday 2 AM
**Version:** v1
**Status:** active

## Purpose
Auto-discover free/preview models across all configured providers. Score against Apple's
adaptive-learner history. Propose chain upgrades via the dream cycle. Replaces hardcoded
model lists with intent-based routing.

## Usage

```bash
# Discover/refresh all provider model lists
phoenix models refresh

# Best model for an intent
phoenix models for "code generation"
phoenix models for "quick-chat"
phoenix models for "deep-reasoning"

# List current manifest
phoenix models list [--provider openrouter|ollama|cerebras|groq|mistral|hf]

# Show intent mapping
phoenix models intents
```

## Intents
- `quick-chat` — fast, cheap, conversational
- `deep-reasoning` — complex multi-step tasks
- `code-gen` — code generation and debugging
- `vision` — image + text
- `audio` — speech, transcription
- `embed` — embedding generation
- `tool-use` — function calling, structured output
- `long-context` — >32K context
- `cheap-bulk` — high-volume low-cost

## Logic (full)
1. For each provider with a live key in `keys/registry.yml`:
   - Hit provider list-models endpoint
   - Filter: free-tier, preview, or unrestricted-access
   - Capture: context window, speed tier, modality, benchmarks, last-updated
2. For local models (Ollama, HF, GGUF):
   - All weights at `D:\PhoenixGlobal\models\ollama\` or `D:\PhoenixGlobal\models\local\`
   - Register in-place; no re-download
   - Prune by disk rule: ≤80% of free D:, never last 10 GB
3. Score each model vs adaptive-learner history (latency, success rate per task type)
4. Propose chain updates → `dream/queue/model-proposal-<date>.md`
5. Apple graduates/rejects at next dream cycle

## Intent mapping
Protocol file: `protocols/model-intent.yml`
Every agent calls `phoenix models for "<intent>"` before any LLM call — no hardcoded strings.
Fallback chain: adaptive-learner best → provider default → local HF.

## Implementation
- `brain/skills/model-discovery/discovery.py` — provider scrapers
- `protocols/model-intent.yml` — intent → model ranking table
- `manifests/models.yml` — canonical model registry (updated by this skill)

## Ollama consolidation (one-time, idempotent)
Migration target: `D:\PhoenixGlobal\models\ollama\`
Idempotency marker: `D:\PhoenixGlobal\models\ollama\.migration\MIGRATED.ok`
Full 13-step procedure documented in Phase 3b.1 of PHOENIX-RUN-2-SPEC.md.
**Status: PENDING** — requires Windows PowerShell execution (robocopy /MOVE).
