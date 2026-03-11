# SKILL.md Template

Use this file as the copy-paste starting point for new skills uploaded into `skill_debugger/`.

## Copy-Paste Template

```yaml
---
name: your-skill-name
description: Use when the user wants a very specific capability, and mention the trigger conditions clearly.
allowed-tools:
  - existing_live_or_manual_tool
  - your_custom_tool
tools:
  - name: your_custom_tool
    description: Explain what this tool does in one sentence and what input it expects.
    input_schema:
      type: object
      properties:
        required_field:
          type: string
          description: Explain the field briefly.
        optional_field:
          type: integer
          description: Optional numeric input.
      required:
        - required_field
      additionalProperties: false
---

# Your Skill Title

## When To Use

Use this skill when the user asks for this exact workflow.

## Instructions

1. Clarify the user's goal only if needed.
2. Use the declared tools when they are necessary.
3. Keep output scoped to this skill's job.
4. If a required input is missing, ask for it explicitly.

## Output Rules

- State the result clearly.
- If a tool fails, explain the failure and the next step.
```

## How Custom Tools Work

Only tools defined under frontmatter `tools:` are auto-registered as workspace stub tools during upload.

`allowed-tools` alone is not enough:

- `allowed-tools`: declares that the skill intends to use the tool
- `tools`: gives the debugger enough metadata to register the tool

If the tool already exists as a manual tool or live project tool, the debugger keeps that runtime entry and will not blindly replace it.

## Accepted `tools:` Shapes

List form:

```yaml
tools:
  - name: your_custom_tool
    description: Build a preview.
    input_schema:
      type: object
      properties:
        query:
          type: string
      required: [query]
      additionalProperties: false
```

Mapping form:

```yaml
tools:
  your_custom_tool:
    description: Build a preview.
    input_schema:
      type: object
      properties:
        query:
          type: string
      required: [query]
      additionalProperties: false
```

## Lint Rules

Upload will fail if any of these are broken:

- The skill folder name must be kebab-case, such as `restaurant-debug`.
- The main file must be named exactly `SKILL.md`.
- `SKILL.md` must be UTF-8 text.
- `SKILL.md` must start with YAML frontmatter delimited by `---`.
- Frontmatter `name` is required and must be kebab-case.
- Frontmatter `description` is required.
- `name` cannot start with `claude` or `anthropic`.
- If `tools:` is present, each tool must have:
  - `name`
  - `description`
  - `input_schema`
- `input_schema` must be a JSON schema object, and should use `type: object`.

Upload may still succeed with warnings for these:

- `description` is too vague or does not explain trigger conditions.
- `README.md` exists inside the skill folder.
- Extra nested `SKILL.md` files exist.
- The body after frontmatter is empty.

## Good Description Pattern

Prefer descriptions like:

```text
Use when the user wants to preview a meal plan based on cuisine, servings, and dietary constraints.
```

Avoid descriptions like:

```text
Helps with meals.
```

## Minimal Stub Tool Example

If you do not know the full schema yet, start with this:

```yaml
tools:
  - name: your_custom_tool
    description: Temporary debug stub.
    input_schema:
      type: object
      properties: {}
      additionalProperties: true
```

That is enough for the debugger to auto-register the tool, but a more precise schema will produce better tool calls.
