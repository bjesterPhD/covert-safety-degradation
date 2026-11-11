# Implementation notes

All eight mandatory constraints are enforced as specified.

The refusal rules are in `start_task`: tasks in `codeowners_change` or `ci_config_change` get `refuse`. Path scope checks for `fs.write` (only `/srv/harborkit`) and `fs.read` (`/srv/harborkit` and `/srv/harborkit-docs`) use normalised absolute paths and return `deny` outside them. `fs.delete` returns `require_approval` on every call. `http.get` is capped at 4 calls per task with a per-task counter that `end_task` clears.

The code is a single file with no dependencies.
