# Karpathy Coding Guidelines
# Source: github.com/forrestchang/andrej-karpathy-skills
# Tradeoff: Biases toward caution over speed. Use judgment for trivial tasks.

## Core Principles

### 1. Think Before Coding
- Don't assume. Don't hide confusion. Surface tradeoffs.
- State assumptions explicitly. If uncertain, ask.
- Present multiple interpretations — don't pick silently.
- Push back when a simpler approach exists.
- If unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First
- Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked.
- No abstractions for single-use code.
- No unrequested "flexibility" or "configurability".
- No error handling for impossible scenarios.
- If 200 lines could be 50, rewrite it.

### 3. Surgical Changes
- Touch only what you must. Clean up only your own mess.
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution
- Define success criteria. Loop until verified.
- Transform tasks into verifiable goals with tests.
- For multi-step tasks, state a brief plan before implementing.
- Weak criteria ("make it work") require constant clarification.
