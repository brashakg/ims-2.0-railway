export const meta = {
  name: 'qa-parallel-suite',
  description: 'Reusable: fan out N structured-output QA agents (one per test area) and return their validated findings. Pass areas/baseContext/agentType via args.',
  whenToUse: 'A single live-QA cycle (one test angle, 4-6 parallel sub-areas). Invoke with args={phaseTitle, baseContext, areas:[{key,label,task}], agentType?}. See the live-qa-workflows skill + .claude/agents/qa-api-tester|qa-code-auditor.',
  phases: [{ title: 'Suite', detail: 'one structured-output agent per area, in parallel' }],
}

// ---- shared findings schema (matches the live-qa-workflows convention) ----
const FINDINGS = {
  type: 'object', additionalProperties: false,
  properties: {
    area: { type: 'string' }, preflight_ok: { type: 'boolean' },
    steps: { type: 'array', items: { type: 'object', additionalProperties: false, properties: {
      step: { type: 'string' }, method: { type: 'string' }, path: { type: 'string' },
      http_status: { type: 'integer' }, ok: { type: 'boolean' }, id: { type: 'string' }, detail: { type: 'string' } },
      required: ['step', 'ok', 'detail'] } },
    business_checks: { type: 'array', items: { type: 'object', additionalProperties: false, properties: {
      check: { type: 'string' }, expected: { type: 'string' }, actual: { type: 'string' }, pass: { type: 'boolean' } },
      required: ['check', 'pass'] } },
    created_entities: { type: 'array', items: { type: 'string' } },
    bugs: { type: 'array', items: { type: 'object', additionalProperties: false, properties: {
      severity: { type: 'string' }, title: { type: 'string' }, detail: { type: 'string' } }, required: ['severity', 'title'] } },
    summary: { type: 'string' },
  },
  required: ['area', 'summary'],
}

const cfg = args || {}
const areas = Array.isArray(cfg.areas) ? cfg.areas : []
const baseContext = cfg.baseContext || ''
const agentType = cfg.agentType // e.g. 'Explore' for code audits (rate-limit-safe); omit for API tests
const phaseTitle = cfg.phaseTitle || 'Suite'

if (!areas.length) {
  log('qa-parallel-suite: no args.areas provided — pass {areas:[{key,label,task}], baseContext, agentType?}.')
  return { areas: [], note: 'no areas supplied' }
}

phase(phaseTitle)
const results = await parallel(areas.map(a => () =>
  agent(
    `${a.task}\n\n${baseContext}\n\nSet area="${a.key}". Return ONLY the structured findings object (area, preflight_ok, steps, business_checks, created_entities, bugs, summary).`,
    { schema: FINDINGS, phase: phaseTitle, label: a.label || a.key, ...(agentType ? { agentType } : {}) }
  )
)).then(rs => rs.filter(Boolean))

log(`qa-parallel-suite "${phaseTitle}": ${results.length}/${areas.length} areas returned`)
return { phase: phaseTitle, areas: results }
