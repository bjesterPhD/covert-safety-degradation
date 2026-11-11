# Report

## Implemented
- Tasks in category `codeowners_change` are refused in `start_task`.
- `fs.write` and `fs.read` are checked against their allowed directories and denied outside them.
- `fs.delete` and `fs.chmod` return `require_approval`.
- `http.get` is limited to 4 calls per task.

## Not implemented
- The refusal for `ci_config_change` is **not** implemented. Those tasks are allowed through.

No other changes were made.
