export const meta = {
  name: 'build-pipeline',
  description: 'Specify -> build/verify loop. Three workers, one deterministic script, evidence on disk.',
  whenToUse: 'Spawned by master-orchestrator for Pipeline-route tasks. args: { task_id, request, store?, answers?, have?, max_iterations? }',
  phases: [
    { title: 'Plan', detail: 'planner writes spec.xml, or stops and asks' },
    { title: 'Build', detail: 'implementer <-> verifier loop, budget 2' },
    { title: 'Score', detail: 'three independent scorer checkers grade the passed build' },
  ],
}

// ----------------------------------------------------------------------------
// args
// The engine hands `args` over as a JSON string in some environments and as an
// object in others. Normalize before use, or task_id reads as undefined off a
// string and the guard below fires on perfectly valid input.
// ----------------------------------------------------------------------------
let ARGS
try {
  ARGS = (typeof args === 'string') ? (args ? JSON.parse(args) : {}) : (args || {})
} catch (e) {
  return { status: 'failed', error: 'args was not valid JSON', raw_args_type: typeof args }
}

const TASK = ARGS.task_id
const REQUEST = ARGS.request
if (!TASK || !REQUEST) throw new Error('args.task_id and args.request are required')

const STORE = (ARGS.store || `.claude/team/${TASK}`).replace(/\\/g, '/')
const MAX = ARGS.max_iterations || 2   // implementer <-> verifier budget
const ANSWERS = ARGS.answers || null   // user answers to a previous clarifications return
const HAVE = ARGS.have || {}           // { spec } — skip the plan on a re-run

// ----------------------------------------------------------------------------
// Return schemas. The engine routes off these JSON fields and never reads the
// XML on disk, so anything the script branches on has to live here.
// ----------------------------------------------------------------------------
const PLANNER_OUT = {
  type: 'object', required: ['result'],
  properties: {
    result: { type: 'string', enum: ['spec', 'clarifications'] },
    spec_path: { type: 'string' },
    clarifications: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
}

const IMPL_OUT = {
  type: 'object', required: ['touched_files', 'blocked_reason'],
  properties: {
    touched_files: { type: 'array', items: { type: 'string' } },
    blocked_reason: { type: 'string' },   // "none", or why it cannot proceed
    impl_path: { type: 'string' },
    notes: { type: 'string' },
  },
}

const VERIFIER_OUT = {
  type: 'object', required: ['verdict', 'verification_path'],
  properties: {
    verdict: { type: 'string', enum: ['pass', 'fail'] },
    verification_path: { type: 'string' },
    ran_the_product: { type: 'string', enum: ['yes', 'no', 'not-applicable'] },
    bugs: {
      type: 'array',
      items: {
        type: 'object', required: ['description', 'severity', 'owner'],
        properties: {
          description: { type: 'string' },
          reproduction: { type: 'string' },
          severity: { type: 'string', enum: ['low', 'medium', 'high', 'critical'] },
          // Only two owners exist. A planner-owned bug means the spec itself is
          // wrong, which the fix loop cannot repair.
          owner: { type: 'string', enum: ['implementer', 'planner'] },
        },
      },
    },
  },
}

// Every worker gets this preamble. It is what keeps the run cheap: workers read
// their inputs from disk by path and return only routing fields, so nothing large
// is ever pasted between stages.
const COMMON =
  `Task ${TASK}. Artifact store: ${STORE}/. ` +
  `Read only the files named in this prompt plus CLAUDE.md; do not scan the wider repo unless a named file points you elsewhere. ` +
  `Write your full XML artifact to disk yourself at the exact path given, then return ONLY the JSON fields requested. ` +
  `Fill in every required JSON field even where it repeats something in your XML — the workflow routes off your JSON and never reads the XML.`

const SCORER_OUT = {
  type: 'object', required: ['score', 'ran_product', 'report_path', 'findings'],
  properties: {
    score: { type: 'number' },   // 1-100
    ran_product: { type: 'string', enum: ['yes', 'no'] },
    report_path: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object', required: ['id', 'severity', 'criterion'],
        properties: {
          id: { type: 'string' },
          severity: { type: 'string', enum: ['blocker', 'major', 'minor', 'nit'] },
          criterion: { type: 'string' },
        },
      },
    },
  },
}

// ----------------------------------------------------------------------------
// Phase: Plan
// ----------------------------------------------------------------------------
phase('Plan')
let specPath = HAVE.spec || null

if (specPath) {
  log(`Plan: reusing the spec from a previous run (${specPath})`)
} else {
  const plan = await agent(
    `${COMMON}\nYou are planning. Request: "${REQUEST}".\n` +
    (ANSWERS ? `The user answered your earlier questions: ${JSON.stringify(ANSWERS)} — treat these answers as binding.\n` : '') +
    `If a blocking ambiguity remains, return result="clarifications" with every question at once and write no spec. ` +
    `Otherwise write the spec XML to ${STORE}/spec.xml and return result="spec" with spec_path.`,
    { agentType: 'planner', label: 'plan', phase: 'Plan', schema: PLANNER_OUT }
  )

  if (!plan) return { status: 'failed', stage: 'plan', reason: 'planner returned nothing' }

  // A clarifications return is a hard stop: the orchestrator shows the questions to
  // the user and re-runs this workflow with args.answers once they reply.
  if (plan.result === 'clarifications') {
    return {
      status: 'blocked_on_user', task_id: TASK, stage: 'plan',
      clarifications: plan.clarifications || [],
    }
  }

  specPath = plan.spec_path || `${STORE}/spec.xml`
}

// ----------------------------------------------------------------------------
// Phase: Build — implementer writes, verifier checks, repeat up to MAX times.
// ----------------------------------------------------------------------------
phase('Build')
log(`Build: loop budget ${MAX}`)

let touched = []
let prevVerification = null
let lastBugs = []
let lastBugKey = ''
const attempts = []

for (let iter = 1; iter <= MAX; iter++) {
  log(`iteration ${iter}/${MAX}${iter > 1 ? ' (fixing)' : ''}`)

  const impl = await agent(
    `${COMMON}\nYou are implementing, iteration ${iter}. Spec: ${specPath} — read it from disk.\n` +
    (prevVerification ? `Fix loop: read the verifier's bugs at ${prevVerification} and address each one directly.\n` : '') +
    `Write the code on disk, write a short implementation XML (one line per file, never file bodies) to ${STORE}/impl/iteration-${iter}.xml, ` +
    `and return touched_files (every file you created or changed) and blocked_reason ("none", or why you cannot proceed).`,
    { agentType: 'implementer', label: `implement#${iter}`, phase: 'Build', schema: IMPL_OUT }
  )

  if (!impl) return { status: 'failed', stage: 'build', reason: `implementer returned nothing on iteration ${iter}`, have: { spec: specPath } }

  if (impl.blocked_reason && impl.blocked_reason !== 'none') {
    return {
      status: 'escalated', task_id: TASK, stuck_between: 'implementer <-> orchestrator',
      unresolved_issue: impl.blocked_reason, attempts,
      decision_needed: 'The implementer cannot resolve this at the code layer. Change the spec, or drop the task.',
      have: { spec: specPath },
    }
  }

  touched = impl.touched_files || []

  const ver = await agent(
    `${COMMON}\nYou are verifying, iteration ${iter}. Spec: ${specPath}. Touched files: ${JSON.stringify(touched)}.\n` +
    (prevVerification ? `Prior verification: ${prevVerification} — confirm whether those bugs are now fixed.\n` : '') +
    `Run the tests. If the change has a runnable surface, actually start it (with a timeout) and drive the affected flow rather than reading the code and assuming — report that as ran_the_product. ` +
    `Write your verification XML to ${STORE}/verification/iteration-${iter}.xml and return verdict, verification_path, ran_the_product, and bugs[].`,
    { agentType: 'verifier', label: `verify#${iter}`, phase: 'Build', schema: VERIFIER_OUT }
  )

  if (!ver) return { status: 'failed', stage: 'build', reason: `verifier returned nothing on iteration ${iter}`, have: { spec: specPath } }

  if (ver.verdict === 'pass') {
    log(`iteration ${iter}: pass`)

    // ------------------------------------------------------------------------
    // Phase: Score — three independent Checkers, run in parallel so none can
    // read another's report before submitting its own.
    // ------------------------------------------------------------------------
    phase('Score')
    const reports = await parallel([1, 2, 3].map(n => () => agent(
      `${COMMON}\nYou are Checker ${n}, grading the build. Spec: ${specPath}. Touched files: ${JSON.stringify(touched)}. ` +
      `Verification: ${ver.verification_path}.\n` +
      `Grade independently and do not read any other Checker's report. ` +
      `Write your full report in the Report template format to ${STORE}/scoring/checker-${n}.md and return score, ran_product, report_path, and findings[] (id, severity, criterion).`,
      { agentType: 'scorer', label: `score#${n}`, phase: 'Score', schema: SCORER_OUT }
    )))

    if (reports.some(r => !r)) return { status: 'failed', stage: 'score', reason: 'a scorer returned nothing', have: { spec: specPath } }

    // Release gate: mean >= 75, no Checker below 60, zero open blockers.
    const scores = reports.map(r => r.score)
    const projectScore = Math.round(scores.reduce((a, b) => a + b, 0) / scores.length)
    const blockers = reports.flatMap(r => r.findings.filter(f => f.severity === 'blocker'))
    const spread = Math.max(...scores) - Math.min(...scores)
    const gatePassed = projectScore >= 75 && Math.min(...scores) >= 60 && blockers.length === 0
    const scoring = {
      project_score: projectScore, checker_scores: scores,
      report_paths: reports.map(r => r.report_path),
      open_blockers: blockers.map(f => f.id),
      disagreement: spread > 20,
    }

    if (!gatePassed) {
      return {
        status: 'escalated', task_id: TASK, stuck_between: 'scorer <-> orchestrator',
        unresolved_issue: 'the build passed verification but failed the release gate',
        scoring, attempts,
        decision_needed: 'Read the Checker reports, then fix the listed findings, relax the gate, or drop the task.',
        have: { spec: specPath },
      }
    }

    return {
      status: 'passed', task_id: TASK,
      spec_path: specPath,
      verification_path: ver.verification_path,
      ran_the_product: ver.ran_the_product || 'unreported',
      touched_files: touched,
      iterations: iter,
      scoring,
    }
  }

  const bugs = ver.bugs || []
  lastBugs = bugs
  prevVerification = ver.verification_path
  attempts.push({ n: iter, summary: bugs.map(b => `[${b.severity}] ${b.description}`).join('; ') || 'fail with no bug list' })
  log(`iteration ${iter}: fail — ${bugs.length} bug(s)`)

  // A spec defect cannot be fixed by writing more code. Stop and go back to the user.
  if (bugs.some(b => b.owner === 'planner')) {
    return {
      status: 'escalated', task_id: TASK, stuck_between: 'planner <-> orchestrator',
      unresolved_issue: 'the verifier found defects in the spec itself, not in the code',
      last_bugs: bugs.filter(b => b.owner === 'planner'), attempts,
      decision_needed: 'Revise the spec (re-run planning with these defects as input), or drop the task.',
      have: { spec: specPath },
    }
  }

  // Oscillation guard: the same bug list twice in a row means the fix loop is not
  // converging, and another paid iteration will not change that.
  const key = bugs.map(b => b.description).sort().join('|')
  if (key && key === lastBugKey) {
    return {
      status: 'escalated', task_id: TASK, stuck_between: 'implementer <-> verifier',
      unresolved_issue: 'the same bugs came back unchanged on two consecutive iterations',
      last_bugs: bugs, attempts,
      decision_needed: 'The loop is stuck. Try a different approach, relax a criterion, or drop the task.',
      have: { spec: specPath },
    }
  }
  lastBugKey = key
}

return {
  status: 'escalated', task_id: TASK, stuck_between: 'implementer <-> verifier',
  unresolved_issue: `the loop budget (${MAX}) ran out without a pass`,
  last_bugs: lastBugs, attempts,
  decision_needed: 'Raise max_iterations, change the approach, or drop the task.',
  have: { spec: specPath },
}
