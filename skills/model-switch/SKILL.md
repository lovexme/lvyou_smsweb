---
name: model-switch
description: Switch the current session model, inspect the current override, reset back to the default model, or spawn a new session on a specified model. Supports local alias/config guidance via models.json. Use when the user asks to switch models, check the active model, compare models, or run a task on another model.
allowed-tools: read, session_status, sessions_spawn
---

# Model Switch

Use this skill whenever the user wants to change which model is used.

## Local files

- Config file: `models.json`
- Optional notes: `README.md`

Before applying alias normalization, read `models.json` if it exists.

## What this skill covers

- Check the active model / current override for this chat
- Switch the **current chat** to another model
- Reset the current chat back to the default model
- Start a **new separate session** on another model
- Use a local alias map instead of hardcoding shorthand in the skill text

## Core rule

Treat model ids as exact strings unless a safe alias mapping exists in `models.json`. Do not invent ids.

## Config behavior

If `models.json` exists, use it as the source of truth for:
- alias normalization
- default keyword mapping
- local notes about uncertain model names

Expected structure:

```json
{
  "version": 1,
  "defaultModel": "default",
  "aliases": {
    "默认": "default",
    "4o": "gpt-4o"
  },
  "notes": {
    "claude sonnet": "Keep as-is unless the exact configured id is known."
  }
}
```

## Decision guide

### Use `session_status` when
- the user says "切到…"
- the user wants this current conversation to use another model
- the user asks what model is active now
- the user wants to reset this conversation to the default model

### Use `sessions_spawn` when
- the user wants a separate run / separate thread / separate worker
- the user mentions running a task on another model without changing the current chat
- the user wants to compare outputs across models
- the user asks for a long or isolated task on another model

## Alias handling

1. Read `models.json` when available.
2. Look up the user's requested shorthand in `aliases`.
3. If there is a clear alias match, use the mapped exact model id.
4. If there is no alias match, keep the user-provided model string unchanged.
5. If `notes` says a label is uncertain, do not silently invent a model id.

## Workflow

### 1) Check current model
Call `session_status` with no model override.
Summarize:
- current model
- whether an override is active
- whether the session is using default behavior

### 2) Switch current chat
- normalize via `models.json` if safe
- call `session_status` with `model: "<resolved-model-id>"`

Then confirm:
- this chat is now using the requested model, or
- the switch failed and the user needs a valid model id

### 3) Reset current chat to default
- prefer the configured `defaultModel` mapping if present
- in practice, reset with `session_status` using `model: "default"`

Then confirm the session is back on the default model behavior.

### 4) Spawn a new session on another model
Call `sessions_spawn` with:
- a concise task
- `model: "<resolved-model-id>"`
- runtime chosen to match the task
- on Discord ACP requests, prefer thread-bound persistent sessions if applicable

Then explain that:
- the new task is running in a separate session
- the current chat model was not changed

## Response style

Be direct and concrete:
- Say what changed
- Name the exact model id used
- Distinguish clearly between **current chat switched** and **new session spawned**

## Ready-made patterns

### Pattern: inspect current model
User: 现在用的什么模型？
Action:
1. call `session_status`
2. summarize the active model and override state

### Pattern: switch current chat
User: 切到 4o
Action:
1. read `models.json`
2. resolve `4o -> gpt-4o`
3. call `session_status` with `model: "gpt-4o"`
4. confirm current chat is switched

### Pattern: reset current chat
User: 切回默认
Action:
1. read `models.json`
2. resolve default keyword if useful
3. call `session_status` with `model: "default"`
4. confirm reset

### Pattern: spawn another model for a task
User: 用 gemini flash 跑这个任务，但别切当前聊天
Action:
1. read `models.json`
2. resolve alias if available
3. call `sessions_spawn` with the user task and resolved model
4. confirm a separate session was created

## Failure handling

If a model switch or spawn fails:
- do not fake success
- report the exact failure briefly
- suggest checking the precise model id or updating `models.json`
- if useful, offer to try again with another exact id
