---
tags: [concept, streaming]
date: 2026-03-28
type: concept
origin: k2b-extract
up: "[[index]]"
---

# Streaming LLM Output

## Summary
Token-by-token streaming from LLM APIs. Reduces perceived latency by showing partial responses as they're generated. Most major providers (Anthropic, OpenAI, MiniMax) support Server-Sent Events for streaming.

## Implementation notes
Node.js handles this well with native fetch + ReadableStream. Useful for any project with user-facing latency concerns.
