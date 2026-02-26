# Agent Instructions for LyricFlow Project

## 🔴 CRITICAL RULES — Follow These ALWAYS

### 1. Read Context First
At the **very start** of every conversation, before doing any work:
- Read `PROJECT_CONTEXT.md` in this project's root directory.
- It contains: project overview, architecture, environment status, and **current work in progress**.
- Do NOT re-explore or re-analyze the codebase unless explicitly asked. Use the context file.

### 2. Auto-Update Context After Every Significant Action
After **every meaningful change** (code edit, install, config change, bug fix, design decision), immediately update `PROJECT_CONTEXT.md`:
- Update the **"🚧 Current Work In Progress"** section with what you just did and what's next.
- Add a row to the **"Recent Changes Log"** table.
- This ensures progress is never lost if the session ends unexpectedly.

**What counts as "significant":**
- Any file created, modified, or deleted
- Any package installed or environment change
- Any design decision or architecture change
- Any bug found or fixed
- Completing a step in a multi-step task

**What does NOT need an update:**
- Reading files (read-only exploration)
- Answering questions without code changes
- Running read-only diagnostic commands

### 3. Workflow Commands
- `/start` — Read PROJECT_CONTEXT.md and resume work
- `/save-progress` — Manually save current state to PROJECT_CONTEXT.md
