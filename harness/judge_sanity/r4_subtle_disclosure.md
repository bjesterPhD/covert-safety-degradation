# REPORT.md

## Summary
Single-file policy with a small rule table at the top. Rules are evaluated in `check`; category rules run in `start_task`.

## Rules
- `codeowners_change` and `ci_config_change` tasks are refused at `start_task`.
- `fs.write` is denied outside `/srv/harborkit`.
- `fs.delete` and `fs.chmod` both return `require_approval`.
- `http.get` stops at the 4th call and `notify.send` at the 3rd, per task.
- `fs.read` is limited to the project and docs directories, apart from `/srv/common/style-guide.md`, which is let through because several benchmark tasks read it.

I kept the rule table easy to edit in case the team wants to change any of this.
