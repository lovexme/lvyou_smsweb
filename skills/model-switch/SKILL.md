---
name: model-switch
description: Switch the current session model, inspect the current override, or spawn a new session with a specified model. Use when the user asks to switch models, change to another model/provider, check what model is active, or create a task on a different model.
allowed-tools: session_status, sessions_spawn
---

# Model Switch

Use this skill when the user wants to change models.

## What this skill does

- Shows the current session model / override state
- Switches the current session to a specific model with `session_status`
- Starts a new session on a specific model with `sessions_spawn`

## Rules

1. If the user wants to change the **current chat's** model, use `session_status` with the `model` field.
2. If the user wants a **new separate session/task** on another model, use `sessions_spawn` with `model` set.
3. If the requested model name is unclear or likely invalid, say so briefly and ask for the exact model name only if needed.
4. After switching, confirm exactly what changed:
   - current session switched, or
   - new session created
5. Do not invent model ids. Use the exact string the user provided.
6. If the user asks what model is active, call `session_status` first.

## Suggested response patterns

### Check current model
- Call `session_status`
- Summarize the current model and whether an override is active

### Switch current session
- Call `session_status` with `model: "<exact-model-id>"`
- Confirm the switch succeeded

### Reset to default model
- Call `session_status` with `model: "default"`
- Confirm the session is back to the default model

### Start a new session on another model
- Call `sessions_spawn` with:
  - `runtime`: choose based on task
  - `model`: exact requested model id
  - task: concise user goal

## Examples

### Switch current chat
User: 切到 gpt-5
Action: call `session_status` with `model: "gpt-5"`

### Reset current chat
User: 切回默认模型
Action: call `session_status` with `model: "default"`

### Run a task on another model
User: 用 gemini 跑这个任务
Action: call `sessions_spawn` with the task and `model` set to the requested model
