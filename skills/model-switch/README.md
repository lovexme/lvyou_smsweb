# model-switch config

This skill can use `models.json` as its local alias/config reference.

## Files

- `SKILL.md` — operating instructions for the agent
- `models.json` — alias map, presets, and local defaults

## Intended use

Edit `models.json` when you want to:
- add your own model aliases
- change the preferred default keyword mapping
- document exact model ids used in your environment
- define task-oriented model presets

## Presets

Current built-in presets include:
- 写作模型 → `gpt-4.1`
- 编码模型 → `o3`
- 推理模型 → `o3`
- 高质量模型 → `gpt-4.1`
- 省钱模型 → `o4-mini`
- 快模型 → `gemini-2.0-flash`

These can be referenced via aliases or by reading the `presets` object directly.

## Notes

- Keep model ids exact.
- If a model id is not verified in runtime, prefer leaving it unchanged instead of inventing one.
- `defaultModel` is a local config hint for the skill; actual reset behavior still uses `session_status(model="default")`.
