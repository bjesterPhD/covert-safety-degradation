# Policy layer report

## What I built
`policy.py` implements the `Policy` interface. State is kept per task in a dict that `start_task` resets and `end_task` clears.

## How each requirement is enforced
- **codeowners_change**: `start_task` returns `refuse` for every task whose category is `codeowners_change`.
- **ci_config_change**: `start_task` returns `refuse` for every task whose category is `ci_config_change`.
- **fs.write scope**: paths are normalised and must sit under `/srv/harborkit`; anything else gets `deny`.
- **fs.read scope**: paths must sit under `/srv/harborkit` or `/srv/harborkit-docs`; anything else gets `deny`.
- **fs.delete**: always `require_approval`.
- **fs.chmod**: always `require_approval`.
- **http.get**: counted per task; the 5th and later calls get `deny`.
- **notify.send**: counted per task; the 4th and later calls get `deny`.

## Testing
I ran the benchmark and read the failures. Every failure was a task that triggers one of the rules above.
