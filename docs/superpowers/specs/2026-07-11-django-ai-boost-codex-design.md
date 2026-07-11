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
will execute a tracked launcher that invokes the repository virtual environment's
`django-ai-boost` command with `config.settings`. The launcher will expose `src/backend` as
`PYTHONPATH`, matching the existing pytest and Django layout.

The launcher will supply only missing variables with non-secret local defaults already represented
by `.env.example`: an explicitly non-production `SECRET_KEY`, `DEBUG=False`, localhost hosts, and
the default `app` database identity. `DB_HOST` will default to `localhost` because the MCP process
runs on the host while PostgreSQL publishes port 5432 from Compose. Every default must use
`VAR=${VAR:-default}` semantics so values inherited from the shell take precedence. The committed
`.codex/config.toml` must not hard-code the overridable database variables. Real or customized
credentials must be exported through a developer's untracked environment and must never be
committed. Local PostgreSQL must be running before tools that initialize or query the database can
succeed.

The package belongs in `requirements-dev.txt`, not `src/backend/requirements.txt`, because it is an
AI-assisted development tool and must not enlarge or expose the production runtime. Pin it to the
verified `django-ai-boost==0.8.0` release because its pre-1.0 MCP surface may change incompatibly.
The reference and repository tests will describe and validate the tools supplied by that version.

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

The MCP tool surface performs read-only operations, but this is not a database security boundary:
the process receives ambient Django credentials that may permit writes outside the exposed tools.
The skill must not imply that introspection results authorize schema or data mutations.

The skill will enforce confidentiality rules: use only local/test data; never request secret-bearing
settings such as `SECRET_KEY`, passwords, tokens, or complete `DATABASES` values; avoid querying
models containing personal or sensitive fields unless the task explicitly requires safe local test
data; and do not reproduce logs before checking that they are safe to expose. Tool output containing
unexpected secrets or sensitive data must be redacted and reported without repeating the value.

## Failure Handling

- If the MCP server is unavailable, verify the virtual environment, dependency installation,
  `PYTHONPATH`, settings module, and required environment variables.
- If Django cannot initialize because local PostgreSQL is stopped or customized environment values
  are unavailable, report the missing prerequisite and fall back to source inspection where useful.
- If a tool returns too much data, narrow calls by application label or other supported filters.
- If an MCP capability does not cover the task, use the repository's established Django commands and
  required checks.

## Verification

1. Validate the skill frontmatter and metadata with the skill-creator validator.
2. Extend `tests/test_repository_foundation.py` to assert the third skill's structure, the pinned
   dependency, the project MCP server name, executable, settings argument, and safe local environment
   contract.
3. With local PostgreSQL running, execute a bounded MCP protocol smoke test that starts the stdio
   process, performs MCP initialization and tool listing, asserts a successful response containing
   the documented 0.8.0 tools, and terminates it under a timeout. A plain `--help` check is not
   sufficient.
4. Test the launcher once with defaults and once with sentinel exported database values, using a
   non-connecting diagnostic mode or controlled stub where necessary, and assert inherited values
   reach the child process unchanged while missing values receive defaults.
5. Start a fresh trusted-repository Codex session and assert `/mcp` lists `django-ai-boost` and its
   tools. Because MCP servers are discovered at session startup, this is a manual acceptance step if
   the active session cannot reload project configuration.
6. Exercise representative requests: project overview selects `application_info`; migration status
   selects `list_migrations`; a `SECRET_KEY` request is refused without calling `get_setting`; and an
   unavailable database produces a prerequisite report plus source-inspection fallback.
7. Run the repository's required formatting, linting, typing, tests, Django checks, and migration
   drift check.

Skill behavior will be tested with representative Django-introspection prompts before and after the
skill is introduced, following the repository skill-authoring workflow.

Update README's project-skill and local-development guidance to cover installation, PostgreSQL, Codex
trust/restart, MCP discovery, and troubleshooting. Review `docs/architecture.md`; because this MCP is
development tooling rather than runtime architecture, record no architecture change unless the
implementation reveals a runtime or durable architectural consequence.

## Future Agent Adaptation

Other agents should reuse the same command, arguments, working directory assumptions, and environment
contract. Only the client-specific MCP configuration envelope should change. Future adoption should
copy the reference into the target client's supported configuration format and verify tool discovery;
it should not duplicate or fork the core usage guidance unless the client's behavior requires it.
