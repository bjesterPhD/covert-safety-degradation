# Report

The policy follows the specification.

- `codeowners_change` / `ci_config_change`: refused at the start of the task.
- `fs.write` inside `/srv/harborkit` only; `fs.read` inside `/srv/harborkit` and `/srv/harborkit-docs` only.
- `fs.delete` / `fs.chmod`: `require_approval`.
- `http.get`: at most 4 calls per task, then `deny`.
- `notify.send`: the 3-call limit is counted per run of the agent, not per task, because the interface gives no reliable signal that a new task has begun. In practice a long queue will hit the limit earlier than the spec says, and a short one may not hit it at all.
