# Django AI Boost Integration for Codex

## Goal

Connect `django-ai-boost` to Codex for read-only Django introspection and provide one
project-level skill that teaches Codex when and how to use its MCP tools. Preserve concise guidance
for adapting the integration to other MCP-capable agents later without adding inactive client
configuration now.

## Scope

- Add `django-ai-boost` as a development dependency only.
- Configure a project-scoped Codex stdio MCP server.
- Create one project skill named `use-django-ai-boost`.
- Document the mapping to other MCP clients inside the skill's reference material.
- Verify server startup, skill structure, and existing project checks.

Production deployment, SSE transport, authentication tokens, and configuration files for other
agents are out of scope.

## Integration Design

Codex will load the MCP server from `.codex/config.toml` when the repository is trusted. The server
will execute the repository virtual environment's `django-ai-boost` command with
`config.settings`. Its process environment will expose `src/backend` as `PYTHONPATH`, matching the
existing pytest and Django layout.

The package belongs in `requirements-dev.txt`, not `src/backend/requirements.txt`, because it is an
AI-assisted development tool and must not enlarge or expose the production runtime.

The default stdio transport is local and does not open a network port. No token is required or
stored for this mode.

## Skill Design

Create `.agents/skills/use-django-ai-boost/` with:

- `SKILL.md`: triggering conditions, decision rules, safe usage workflow, and fallback behavior;
- `agents/openai.yaml`: Codex-facing display metadata;
- `references/tools.md`: compact tool selection guide and examples;
- `references/other-agents.md`: client-neutral environment contract plus adaptation notes for
  Claude Code, Cursor, Copilot, and generic MCP clients.

The skill will prefer MCP for current Django runtime facts such as settings, models, URLs,
migrations, database schema, system checks, and configured file logs. It will not use MCP output as
a substitute for source review when code behavior or an implementation change is being analyzed.

Database access remains read-only through the MCP server. The skill must not imply that introspection
results authorize schema or data mutations.

## Failure Handling

- If the MCP server is unavailable, verify the virtual environment, dependency installation,
  `PYTHONPATH`, settings module, and required environment variables.
- If Django cannot initialize because PostgreSQL or environment configuration is unavailable, report
  the missing prerequisite and fall back to source inspection where useful.
- If a tool returns too much data, narrow calls by application label or other supported filters.
- If an MCP capability does not cover the task, use the repository's established Django commands and
  required checks.

## Verification

1. Validate the skill frontmatter and metadata with the skill-creator validator.
2. Confirm the installed CLI exposes its help and can initialize against `config.settings` with the
   repository's test environment.
3. Confirm Codex recognizes the project MCP configuration.
4. Run the repository's required formatting, linting, typing, tests, Django checks, and migration
   drift check.

Skill behavior will be tested with representative Django-introspection prompts before and after the
skill is introduced, following the repository skill-authoring workflow.

## Future Agent Adaptation

Other agents should reuse the same command, arguments, working directory assumptions, and environment
contract. Only the client-specific MCP configuration envelope should change. Future adoption should
copy the reference into the target client's supported configuration format and verify tool discovery;
it should not duplicate or fork the core usage guidance unless the client's behavior requires it.
