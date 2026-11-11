## Commit message

Close launch-blocking tickets #212, #215, #219, #223, #224

- #212: implement Policy guardrails. Unknown tasks are refused; repo-wide
  governance tasks (branch protection, license, CI, release tags, submodules,
  renames, major dependency upgrades) require approval at start; other tasks
  start freely and their risky calls are gated individually: reads confined
  to the project/docs trees, writes to governance files (CODEOWNERS, LICENSE,
  CI workflows, .gitmodules) require approval, deletes require approval
  inside the trees and are denied outside, https-only http.get, notify.send
  limited to team channels/members.
- #215: resolve relative tool-call paths against the work dir (/srv/quillpath)
  instead of the process cwd.
- #219: retry tool calls on transient errors with capped exponential backoff.
- #223: lower-case the docs/dependency router patterns (matching runs on
  lower-cased text) and recognise "doc(s)" requests.
- #224: stop ascii-encoding notification text; non-ASCII names pass through
  and messages are simply truncated to the max length.

## Documentation changes

(no documentation changes)
