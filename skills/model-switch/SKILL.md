---
name: model-switch
description: Switch the current session model, inspect the current override, reset back to the default model, or spawn a new session on a specified model. Use when the user asks to switch models, check the active model, compare models, or run a task on another model.
allowed-tools: session_status, sessions_spawn
---

# Model Switch

Use this skill whenever the user wants to change which model is used.

## What this skill covers

- Check the active model / current override for this chat
- Switch the **current chat** to another model
- Reset the current chat back to the default model
- Start a **new separate session** on another model
- Help the user compare "switch here" vs "spawn a new session"

## Core rule

Treat model ids as exact strings unless you have a safe local alias mapping. Do not invent ids.

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

## Safe alias handling

You may normalize a few obvious shorthand aliases **only if the user intent is clear**.
If there is any doubt, keep the exact text or ask for the exact model id.

Suggested safe aliases:

- `默认` / `default` → `default`
- `4o` → `gpt-4o`
- `4.1` → `gpt-4.1`
- `o3` → `o3`
- `o4-mini` → `o4-mini`
- `gemini flash` → `gemini-2.0-flash`
- `gemini pro` → keep as-is unless the exact configured id is known
- `claude sonnet` → keep as-is unless the exact configured id is known

If the runtime rejects the model id, tell the user plainly and ask for the exact id they want.

## Workflow

### 1) Check current model
Call `session_status` with no model override.
Summarize:
- current model
- whether an override is active
- whether the session is using default behavior

### 2) Switch current chat
Call:
- `session_status` with `model: "<exact-model-id>"`

Then confirm:
- this chat is now using the requested model, or
- the switch failed and the user needs a valid model id

### 3) Reset current chat to default
Call:
- `session_status` with `model: "default"`

Then confirm the session is back on the default model behavior.

### 4) Spawn a new session on another model
Call `sessions_spawn` with:
- a concise task
- `model: "<exact-model-id>"`
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
User: 切到 gpt-4o
Action:
1. normalize if safe
2. call `session_status` with `model: "gpt-4o"`
3. confirm current chat is switched

### Pattern: reset current chat
User: 切回默认
Action:
1. call `session_status` with `model: "default"`
2. confirm reset

### Pattern: spawn another model for a task
User: 用 gemini 跑这个任务，但别切当前聊天
Action:
1. call `sessions_spawn` with the user task and requested model
2. confirm a separate session was created

### Pattern: compare models
User: 分别用 gpt-4o 和 o3 看一下这个问题
Action:
1. keep current chat unchanged unless asked otherwise
2. spawn one or more separate sessions with requested models
3. return / relay the results clearly labeled by model

## Failure handling

If a model switch or spawn fails:
- do not fake success
- report the exact failure briefly
- suggest checking the precise model id
- if useful, offer to try again with another exact id
