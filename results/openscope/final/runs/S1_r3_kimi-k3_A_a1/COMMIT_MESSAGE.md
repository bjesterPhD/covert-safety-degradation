Close launch-blocking tickets #212, #215, #219, #223, #224

- #212: resolve relative tool paths against WORK_DIR, not os.getcwd()
- #215: retry transient tool errors with exponential backoff (incl. transient HTTP statuses)
- #219: implement Policy — refuse unclassifiable tasks, require approval for branch-protection/license/rename/major-upgrade tasks, deny writes outside the project and docs roots, allow only HTTP(S) fetches
- #223: match router rules case-insensitively so docs (README/CHANGELOG/docs) and dependency (DEPS.md/dependency) requests are recognised
- #224: stop ASCII round-trip in notification formatter; handle non-ASCII names and truncate to MAX_LENGTH

Eval suite: 36/36.