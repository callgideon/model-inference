export const meta = {
  name: 'finish-verify',
  description: 'Complete an interrupted verify-lane fix round for one lane, then re-verify each blocking/major finding',
  phases: [{ title: 'Finish fix' }, { title: 'Re-verify' }],
}
// args: {lane, task, branch, worktree, head_before_fix, fix_head, findings_path, evidence, rules, notes}
const L = args
const toFix = L.to_fix
const common = `You are an Opus verifier for lane ${L.lane} (task ${L.task}) on branch ${L.branch}, worktree ${L.worktree}.
Lane rules: ${L.rules} (read them first; obey isolation: task-local ports via INFRX_D_TASK=${L.lane.toLowerCase()}, never 55432; never push/rebase/reset/amend/stash; no hosted DB, box, AWS, SSM; secrets never printed).
Evidence file(s): ${L.evidence}. Head before the fix round: ${L.head_before_fix}. Fix-round commits so far end at ${L.fix_head} (the previous fix agent was killed mid-round; its commits are on the branch).
Coordinator notes: ${L.notes}
The full findings (all lenses, with evidence and proposed fixes) are in the JSON file ${L.findings_path} (keys "all" and "to_fix"); read it first.
Your final text is data for the coordinator, not prose for a human.`

phase('Finish fix')
const fixed = await agent(`${common}

You are completing the ONE fix round for this lane. The blocking/major findings from the three verification lenses are: ${JSON.stringify(toFix)}.
For each finding: check whether the existing fix-round commits (git log ${L.head_before_fix}..HEAD in the worktree) already close it (read the diff and run the regression); if not, fix it now in the worktree on branch ${L.branch} (owned paths only; wiring outside owned paths becomes a wiring request), with a regression test that fails with the fix reverted and, where the finding names a mutant, the mutant added to the lane's list and killed. Rerun the lane's suites on the task-local harness. Append a "Fix round (completed <UTC>)" section to the evidence file (append-only; earlier text kept) with per-finding disposition, fails-before/passes-after, counts, and any finding you could NOT fix (say why); write research/plan/evidence/coordinator/updates/${L.lane}-<UTC>.json (schema in updates/README.md: activity review, head, estimate, blockers, next_action, wiring_requests). Commit everything on the branch (never push). Return JSON with the fields: head (final commit), results: [{id, fixed: bool, note}], counts: {suite: result}, wiring_requests: [..].`, {
  label: `fix:${L.lane}`, phase: 'Finish fix',
  schema: { type: 'object', properties: { head: { type: 'string' }, results: { type: 'array', items: { type: 'object', properties: { id: { type: 'string' }, fixed: { type: 'boolean' }, note: { type: 'string' } }, required: ['id', 'fixed', 'note'] } }, counts: { type: 'object' }, wiring_requests: { type: 'array', items: { type: 'string' } } }, required: ['head', 'results'] },
})

phase('Re-verify')
const rechecks = await parallel(toFix.map(f => () =>
  agent(`${common}

Re-verify ONLY this finding after the completed fix round (new head ${fixed?.head}): ${JSON.stringify(f)}. The fix agent reported: ${JSON.stringify((fixed?.results || []).find(r => r.id === f.id) || null)}. Rerun its reproduction/mutant in a scratch copy (git archive of the head into your scratchpad; do NOT edit the lane worktree); confirm the regression test fails with the fix reverted and passes at the head; confirm owned paths only. Return JSON {fixed: bool, note, regression_test}.`, {
    label: `recheck:${L.lane}:${f.id}`, phase: 'Re-verify',
    schema: { type: 'object', properties: { fixed: { type: 'boolean' }, note: { type: 'string' }, regression_test: { type: 'string' } }, required: ['fixed', 'note'] },
  })))
const open = toFix.filter((f, i) => !(rechecks[i] && rechecks[i].fixed))
return { lane: L.lane, verdict: open.some(f => f.severity === 'blocking') ? 'REJECT' : 'ACCEPT_WITH_FIXES', head: fixed?.head, findings: L.all, fix: fixed, rechecks, open }
