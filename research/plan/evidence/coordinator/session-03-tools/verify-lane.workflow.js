export const meta = {
  name: 'verify-lane',
  description: 'Adversarially verify one lane handback (3 lenses), one fix round, targeted re-verify',
  phases: [{ title: 'Verify' }, { title: 'Fix' }, { title: 'Re-verify' }],
}
// args: {lane, task, branch, worktree, base, head, evidence, brief, owned_paths, rules, notes}
const L = args
const FINDINGS = {type:'object', properties:{
  verdict:{type:'string', enum:['ACCEPT','ACCEPT_WITH_FIXES','REJECT']},
  findings:{type:'array', items:{type:'object', properties:{
    id:{type:'string'}, severity:{type:'string', enum:['blocking','major','minor']},
    title:{type:'string'}, file:{type:'string'}, evidence:{type:'string'}, fix:{type:'string'},
    mutant:{type:'string'}}, required:['id','severity','title','file','evidence']}},
  test_counts:{type:'string'}, mutants_survived:{type:'array', items:{type:'string'}},
  rules_violations:{type:'array', items:{type:'string'}}}, required:['verdict','findings']}
const VERDICT = {type:'object', properties:{fixed:{type:'boolean'}, note:{type:'string'}, regression_test:{type:'string'}}, required:['fixed','note']}
const common = `Lane ${L.lane} (task ${L.task}) handback: branch ${L.branch}, worktree ${L.worktree}, base ${L.base}, head ${L.head}. Evidence file: ${L.evidence}. Brief: ${L.brief}. Owned paths: ${L.owned_paths}. Lane rules: ${L.rules}. Coordinator notes: ${L.notes}.
Work READ-ONLY in that worktree except for scratch files under /tmp (never commit, never touch other worktrees). Run the lane's own commands from its evidence file plus your own; use INFRX_D_TASK=${L.task.toLowerCase()} for any real-service suite. Report file:line evidence for every finding; no finding without a reproduction command or a failing assertion. Default to REJECT only for blocking findings (acceptance criterion not met, oracle absent, test that cannot fail, secrets, isolation breach, unowned path edited, product invariant regressed).`
phase('Verify')
const lenses = [
  `Lens CORRECTNESS+MUTATION: try to REFUTE the handback. For each acceptance criterion and failure oracle in the brief and tasks.json record, find the test that enforces it and BREAK the implementation (edit in the worktree, run the test, revert with git checkout -- <file>) — a test that stays green with the seam removed is a blocking finding. List every mutant you tried and whether it was killed.`,
  `Lens RULES+ISOLATION+SCOPE: check the lane obeyed the lane rules: only owned paths changed (git diff --stat ${L.base}..${L.head}), no push/rebase, no secrets or DSNs with passwords in files/logs/evidence, task-local ports/containers only, no hosted DB/box/AWS access, wiring requests instead of edits to composition/contracts/Makefile/tasks.json, evidence file + updates JSON present and truthful (counts match a rerun), product invariants (82 s cap, CREDIT/USD separation, no ZDR/120 s claims, PostgreSQL authority) intact, no rebuilt earlier-wave module.`,
  `Lens ACCEPTANCE-COVERAGE+INTEGRATION: compare the delivered slices to the brief's numbered deliverables and the manifest's implementation_slices/acceptance; list what is missing or only claimed; check the interfaces the sibling lanes depend on (ports/DTOs/adapters named in the handback) actually exist with the promised signatures; check compatibility with the integration branch (git merge-tree of ${L.branch} onto claude/consumer-v1: report conflicts); check tests are deterministic (run the new tests twice).`,
]
const results = (await parallel(lenses.map((lens, i) => () =>
  agent(`${common}\n\n${lens}\n\nReturn the structured findings.`, {label:`verify:${L.lane}:${i}`, phase:'Verify', schema:FINDINGS, model:'opus', effort:'high'})))).filter(Boolean)
const all = results.flatMap((r, i) => r.findings.map(f => ({...f, id:`${i}-${f.id}`})))
const blocking = all.filter(f => f.severity === 'blocking')
const major = all.filter(f => f.severity === 'major')
log(`${L.lane}: ${all.length} findings (${blocking.length} blocking, ${major.length} major); verdicts ${results.map(r => r.verdict).join(',')}`)
if (!blocking.length && !major.length) return {lane:L.lane, verdict:'ACCEPT', results}
phase('Fix')
const toFix = [...blocking, ...major]
const fixed = await agent(`${common}\n\nYou are the ONE fix round for this lane. Fix these findings in the worktree ${L.worktree} on branch ${L.branch} (commit on that branch; never push): ${JSON.stringify(toFix)}. For each: reproduce, add/adjust the regression at the seam so it FAILS before and PASSES after, fix in the owning module (owned paths only; anything else becomes a wiring request in the evidence file), rerun the lane's focused suites, append a "Fix round" section to the evidence file with the new head and counts, and write updates/${L.task}-<ts>.json. Return the list of finding ids with fixed true/false and the new head SHA in note.`, {label:`fix:${L.lane}`, phase:'Fix', schema:{type:'object', properties:{head:{type:'string'}, results:{type:'array', items:{type:'object', properties:{id:{type:'string'}, fixed:{type:'boolean'}, note:{type:'string'}}, required:['id','fixed']}}}, required:['head','results']}, model:'opus'})
phase('Re-verify')
const rechecks = await parallel(toFix.map(f => () =>
  agent(`${common}\n\nRe-verify ONLY this finding after the fix round (new head ${fixed?.head}): ${JSON.stringify(f)}. Rerun its reproduction/mutant; confirm the regression test fails with the fix reverted (git stash is forbidden — use git diff/apply -R on the specific hunk in a scratch copy, or read the test and the diff). Return fixed=true only if you observed it.`, {label:`recheck:${L.lane}:${f.id}`, phase:'Re-verify', schema:VERDICT, model:'opus'})))
const open = toFix.filter((f, i) => !(rechecks[i] && rechecks[i].fixed))
return {lane:L.lane, verdict: open.some(f => f.severity==='blocking') ? 'REJECT' : 'ACCEPT_WITH_FIXES', head: fixed?.head, findings: all, fix: fixed, rechecks, open}
