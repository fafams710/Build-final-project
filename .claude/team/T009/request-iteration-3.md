# T009 iteration 3 - verifier failures from iteration 2 (user-approved)

HARD CONSTRAINT: do NOT run `git commit`, `git push`, `git stash`, `git checkout -- <file>`, `git restore`, `git reset`,
or anything that changes git history or discards working-tree changes. Only edit local files. Do not edit
`.claude/team/*/baseline*`, `revised-planning.md` or `CLAUDE.md`. Use precise edits. Run pytest as
`python -X faulthandler -m pytest -o faulthandler_timeout=120`; leave no python/postgres process you started running.

Background: `.claude/team/T009/request-iteration-2.md`, `impl/iteration-2.xml`, `verification/iteration-2.xml`.
Files before this iteration: `.claude/team/T009/baseline-3/`.

Fix exactly these two things, tests first (record that the new tests fail on the current code):

## 1. GET /api/rules must skip malformed rows (verifier bug 1, user said yes)
`Database.list_rules` (db.py ~223-237) builds every row with `Rule(**dict(r))`, so one malformed `alert_rules` row
makes `GET /api/rules` return 503 for as long as the row exists. Build rows one at a time the same way
`load_rules` does (share one small helper rather than duplicating the skip logic), skip a row that raises
`ValueError` (pydantic ValidationError is one) with a WARNING naming its id/symbol and the error, and return the
valid rules. Note: the SQL `LIMIT` counts skipped rows, so the endpoint may return fewer than `limit` rules; this is
accepted - say so in a short comment. A real query error still raises `DatabaseUnavailable`/503 as today.
Tests: offline (stub pool or fake) and real-Postgres (insert a malformed row via SQL, GET /api/rules -> 200 with only
the valid rules, WARNING logged).

## 2. Credential masking must not garble messages (verifier bug 2, user said yes)
`Database._describe_error` (db.py ~94-105) replaces every occurrence of the bare password in the message, so a short
password like `x` turns "does not exist" into "does not e***ist". Mask only: the full DATABASE_URL, and the
credential part of it (`user:password@` -> `***@`, or the netloc's userinfo). Do not replace the bare password
string elsewhere in the text. Keep "DATABASE_URL is empty" handling.
Tests: with password `x` and a message containing "exist" the message is unchanged; a message containing the full
URL or `user:password@` is masked; the password never appears via the URL forms.

## Done when
Offline pytest passes; real-Postgres pytest on a throwaway PG17 cluster ("C:/Program Files/PostgreSQL/17/bin",
initdb trust auth, temp dir outside the repo, port 55503) passes, 0 skipped, then stopped and deleted. Report in
`.claude/team/T009/impl/iteration-3.xml`.
