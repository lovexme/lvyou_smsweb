# model-switch config

This skill can use `models.json` as its local alias/config reference.

## Files

- `SKILL.md` — operating instructions for the agent
- `models.json` — alias map and local defaults

## Intended use

Edit `models.json` when you want to:
- add your own model aliases
- change the preferred default keyword mapping
- document exact model ids used in your environment

## Notes

- Keep model ids exact.
- If a model id is not verified in runtime, prefer leaving it unchanged instead of inventing one.
- `defaultModel` is a local config hint for the skill; actual reset behavior still uses `session_status(model="default")`.
