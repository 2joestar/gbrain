# LLM Council — Multi-Agent Deliberation

**Trigger:** `/council <question>` on Telegram or `phoenix council "<question>"`
**Version:** v1
**Status:** active
**Source:** Adapted from @charliejhills LLM Council Claude Code skill

## Purpose
Run complex decisions through a 5-advisor council. Each advisor attacks the question from a different angle (Optimist, Skeptic, Pragmatist, Innovator, Ethicist). They peer-review each other anonymously. A Chairman synthesizes the final verdict.

## Usage
```
/council Should I use Redis or PostgreSQL for caching?
/council Is now a good time to deploy to production?
/council Which AI model should I use for this task?
```

## Architecture
```
Question → 5 Advisors (parallel) → Anonymous Peer Review → Chairman Synthesis → Verdict
```
