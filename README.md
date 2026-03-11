# Skill Debugger

Independent skill debug workspace for product teams.

## What it does

- Upload full skill directories, zip bundles, or standalone `SKILL.md` files into a persistent workspace
- Clear conversation context without deleting uploaded skills
- Register workspace-level tools independently from uploaded skills
- Switch between:
  - `Agent Routing`: Claude decides whether any uploaded project skill applies
  - `Forced Skill`: the runtime exposes only one chosen project skill for isolated testing
- Run chat on top of Claude Agent SDK
- Record native skill reads plus downstream tool calls in a trace panel

## Important runtime behavior

- The debugger now follows Claude-native project skill loading:
  - uploaded skill packages are written into the workspace `.claude/skills/` directory
  - Claude discovers skills through `setting_sources=["project"]`
  - skill activation happens through native `SKILL.md` reads instead of a custom `activate_skill` tool
  - `allowed-tools` in frontmatter remain skill-declared metadata
  - if a skill frontmatter includes a `tools:` section, those tool definitions are auto-registered into the workspace as stub tools during upload
  - skill-defined tools need their own `description` and `input_schema`; otherwise upload lint will fail
  - tool access is global inside the debugger; skill activation does not gate tool visibility
- Workspace tools are currently debug stubs:
  - the tool name stays identical to production
  - arguments are preserved and logged
  - no production backend is called

This is intentional for early product debugging: the platform helps validate trigger decisions, skill selection, and tool argument shape before real backend wiring.

## Run

```bash
uvicorn skill_debugger.app:app --reload --port 8011
```

Open `http://127.0.0.1:8011`.

## OpenRouter

The debugger reads its own `skill_debugger/.env`.

If you want the debugger to use Claude Agent SDK through OpenRouter, and independently execute Google Maps / Composio Yelp tools from this subproject, put these values in `skill_debugger/.env`:

```dotenv
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api
SKILL_DEBUGGER_MODEL=anthropic/claude-opus-4.6
GOOGLE_MAPS_API_KEY=...
COMPOSIO_API_KEY=...
COMPOSIO_USER_ID=default
```

The app maps them to the Anthropic-compatible environment variables required by Claude Code / Claude Agent SDK.
Google Maps and Yelp tool loading no longer depends on the repo-root `config.py` or `src/*` modules.

## Skill-Defined Tools

If a new skill introduces custom tools, define them in `SKILL.md` frontmatter:

```yaml
---
name: meal-tool-debug
description: Use when the user wants to test a custom meal planning tool in the debugger.
allowed-tools: [plan_meal_preview]
tools:
  - name: plan_meal_preview
    description: Build a meal plan preview from cuisine and servings.
    input_schema:
      type: object
      properties:
        cuisine:
          type: string
        servings:
          type: integer
      required: [cuisine]
      additionalProperties: false
---
```

On upload, the debugger will:
- discover tool names from both `allowed-tools` and `tools`
- auto-register tools defined under `tools`
- preserve existing manual/live tools when names collide

## Files

- `skill_debugger/app.py`: FastAPI app and API routes
- `skill_debugger/service.py`: Claude-native runtime orchestration, forced-skill projection, and trace collection
- `skill_debugger/project_tool_runtime.py`: standalone Google Maps / Composio Yelp runtime
- `skill_debugger/google_maps_tools.py`: local Google Maps direct tool implementations
- `skill_debugger/skill_registry.py`: uploaded `SKILL.md` parser
- `skill_debugger/store.py`: workspace/session persistence and `.claude/skills` storage layout
- `skill_debugger/static/*`: frontend UI
