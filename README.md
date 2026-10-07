# faucet skills

Agent Skills that teach coding agents (Claude Code, Codex and any tool that reads the [Agent Skills](https://agentskills.io/) format) to work with [faucet-stream](https://github.com/faucet-hq/faucet-stream): write pipeline configs that pass validation, find out why a run failed, use and publish Template Hub templates, and build connector crates.

The skills follow faucet's own safety loop rather than guessing: read the connector schema, write the YAML, `faucet validate --no-secrets`, `faucet doctor`, `faucet test`, then run. A CI job checks every command, flag and example config in this repo against the latest `faucet` release every day, so the skills don't drift from the CLI.

## Install

**Claude Code**

```
/plugin marketplace add faucet-hq/faucet-skills
/plugin install faucet@faucet
```

**Codex**

```bash
codex plugin marketplace add faucet-hq/faucet-skills
codex plugin add faucet@faucet
```

**Any agent, via the skills registry**

```bash
npx -y skills add faucet-hq/faucet-skills
```

You also need the `faucet` CLI on your `PATH`. Install it with either:

```bash
# Homebrew (macOS / Linux)
brew install faucet-hq/faucet-stream/faucet-cli

# Installer script (macOS / Linux)
curl --proto '=https' --tlsv1.2 -LsSf https://github.com/faucet-hq/faucet-stream/releases/latest/download/faucet-cli-installer.sh | sh
```

## Skills

| Skill | Use it to |
|---|---|
| [`faucet-pipelines`](skills/faucet-pipelines/SKILL.md) | Write or change a pipeline: pick connectors, incremental sync and CDC, write modes, transforms, masking, quality checks, contracts, secrets, scheduling. |
| [`faucet-debug`](skills/faucet-debug/SKILL.md) | Find out why a run failed, is slow or lagging, or wrote the wrong rows, using `doctor`, `status`, the dead-letter queue, state, metrics, `verify` and `rollback`. |
| [`faucet-templates`](skills/faucet-templates/SKILL.md) | Run a Template Hub template, or write, test, version and publish one. |
| [`faucet-connector`](skills/faucet-connector/SKILL.md) | Build a `faucet-source-*` or `faucet-sink-*` crate on `faucet-core` and pass the conformance battery. |
| [`faucet-migrate`](skills/faucet-migrate/SKILL.md) | Move a Meltano, Singer or Airbyte setup to faucet: map each tap and target, bridge the rest through the Singer source, carry state over, and cut over after a side-by-side `faucet verify`. |
| [`faucet-deploy`](skills/faucet-deploy/SKILL.md) | Run faucet in production: choose `run`, `schedule` or `serve`, set up auth and roles, Kubernetes and Helm, state backends, metrics and alerts, and hardening. |

## faucet MCP server

faucet also has an MCP server (`faucet mcp`) that lets an agent list connectors, read schemas, scaffold, validate and preview configs as tools. The prebuilt release binary doesn't include it yet; build it with:

```bash
cargo install faucet-cli --features mcp
claude mcp add faucet -- faucet mcp
```

The skills work without it.

## Contributing

Each skill is a folder under `skills/` with a `SKILL.md`, focused `references/`, and runnable `examples/`. Before opening a pull request, run the same check CI runs:

```bash
python3 scripts/check_skills.py --faucet faucet
```

## License

Apache-2.0.
