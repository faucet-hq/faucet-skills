# faucet skills have moved

The faucet agent skills now ship inside the engine repository, so each faucet release carries the skills that match it:

**https://github.com/faucet-hq/faucet-stream/tree/main/skills**

Install guide: https://faucet-hq.github.io/faucet-stream/getting-started/agent-skills.html

## Switching an existing install

**Claude Code**

```
/plugin marketplace remove faucet
/plugin marketplace add faucet-hq/faucet-stream
/plugin install faucet@faucet
```

**Codex**

```bash
codex plugin marketplace add faucet-hq/faucet-stream
codex plugin add faucet@faucet
```

This repository is no longer edited. Its contents stay here, frozen, so existing links keep working; changes go to `skills/` in faucet-stream.
