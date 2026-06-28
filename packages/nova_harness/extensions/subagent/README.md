# Subagent Extension

Official Nova extension that lets an agent delegate tasks to other installed agents.

## Installation

Copy or symlink this directory into your agent extensions path:

```bash
mkdir -p ~/.nova/agent/extensions
ln -sf /path/to/nova_harness/extensions/subagent ~/.nova/agent/extensions/subagent
```

Or add the absolute path to `Settings.extensions`.

## Usage in an agent

Add `"subagent"` to the agent's `tools.json`:

```json
[
  {"name": "read"},
  {"name": "write"},
  {"name": "subagent"}
]
```

Then the agent can call the tool:

```json
{
  "agent": "scout",
  "task": "Find all authentication-related code"
}
```

## Modes

### Single
```json
{ "agent": "scout", "task": "find auth code", "cwd": "/project" }
```

### Parallel
```json
{
  "tasks": [
    { "agent": "scout", "task": "find models" },
    { "agent": "scout", "task": "find providers" }
  ]
}
```

### Chain
```json
{
  "chain": [
    { "agent": "scout", "task": "find auth code" },
    { "agent": "planner", "task": "Plan refactor using: {previous}" },
    { "agent": "worker", "task": "Implement the plan: {previous}" }
  ]
}
```

## Writing subagent-friendly agents

Use YAML frontmatter in `description.md`:

```markdown
---
name: scout
description: Fast codebase recon
model: claude-haiku-4-5
subagents: []
tools: [read, grep, find, ls, bash]
---

You are a scout...
```

- `model`: preferred default model for this agent
- `subagents`: list of agent names this agent is allowed to delegate to
- `tools`: tool whitelist (merged with `tools.json`)
