export const meta = {
  name: 'design-council',
  description: 'Reusable council: Understand (map current state) -> Propose (N independent architects, distinct lenses) -> Synthesize (one chair adopts the strongest, grafts the rest, corrects against the code). For design/architecture/merge decisions ("consult council").',
  whenToUse: 'A design or architecture decision needing multiple independent perspectives + a synthesized recommendation with a phased plan + open questions. Invoke with args={topic, context, understand:[{label,task}], lenses:[{lens,focus}], synthInstruction, agentType?}. See the live-qa-workflows skill.',
  phases: [
    { title: 'Understand', detail: 'map the current state of the subject' },
    { title: 'Propose', detail: 'independent architects, one per lens' },
    { title: 'Synthesize', detail: 'chair adopts the strongest + grafts the rest' },
  ],
}

const MAP = { type: 'object', additionalProperties: false, properties: {
  surface: { type: 'string' }, summary: { type: 'string' },
  facts: { type: 'array', items: { type: 'string' } },
  overlaps_and_gaps: { type: 'array', items: { type: 'string' } } },
  required: ['surface', 'summary'] }

const PROPOSAL = { type: 'object', additionalProperties: false, properties: {
  lens: { type: 'string' }, vision: { type: 'string' },
  recommendation: { type: 'string' },
  phased_plan: { type: 'array', items: { type: 'string' } },
  what_to_retire: { type: 'array', items: { type: 'string' } },
  tradeoffs: { type: 'string' }, risks: { type: 'array', items: { type: 'string' } },
  summary: { type: 'string' } },
  required: ['lens', 'recommendation', 'summary'] }

const SYNTH = { type: 'object', additionalProperties: false, properties: {
  recommended_architecture: { type: 'string' },
  migration_phases: { type: 'array', items: { type: 'string' } },
  retire_list: { type: 'array', items: { type: 'string' } },
  open_questions: { type: 'array', items: { type: 'string' } },
  summary: { type: 'string' } },
  required: ['recommended_architecture', 'summary'] }

const cfg = args || {}
const topic = cfg.topic || 'the subject'
const context = cfg.context || ''
const understand = Array.isArray(cfg.understand) && cfg.understand.length ? cfg.understand
  : [{ label: 'map', task: `Map the current state of ${topic}: components/files, behavior, fields/endpoints, and where things overlap, duplicate, or have gaps. Be concrete and ground in file:line.` }]
const lenses = Array.isArray(cfg.lenses) && cfg.lenses.length ? cfg.lenses
  : [
      { lens: 'USER-WORKFLOW-FIRST', focus: 'Optimize for how the user actually does the job day-to-day; minimize manual steps; one coherent flow.' },
      { lens: 'TECHNICAL-ARCHITECTURE-FIRST', focus: 'One source of truth + one contract; eliminate divergent/duplicate paths; behavior-preserving additive changes.' },
      { lens: 'RISK/MIGRATION-FIRST', focus: 'Smallest safe steps, backward compatibility, tests first, what could break and how to de-risk.' },
    ]
const synthInstruction = cfg.synthInstruction || 'Resolve disagreements, pick the strongest design, graft the best ideas from each lens, and CORRECT any proposal that conflicts with the actual code. Produce the recommended architecture, a phased migration plan, what to retire, and the open questions that need the owner to decide.'
const agentType = cfg.agentType // 'Explore' for code-grounded councils (recommended)

phase('Understand')
const maps = await parallel(understand.map(u => () =>
  agent(`${u.task}\n\n${context}\n\nReturn ONLY the structured map object.`,
    { schema: MAP, phase: 'Understand', label: u.label || 'map', ...(agentType ? { agentType } : {}) })
)).then(rs => rs.filter(Boolean))
const ctx = `${context}\n\nCURRENT-STATE MAPS:\n${JSON.stringify(maps).slice(0, 12000)}`

phase('Propose')
const proposals = await parallel(lenses.map(L => () =>
  agent(`You are an architect on a design COUNCIL deciding: ${topic}. Your lens: ${L.lens}. ${L.focus}\n\n${ctx}\n\nPropose a concrete, opinionated design from your lens: recommendation, phased plan, what to retire, tradeoffs, risks. Return ONLY the structured object.`,
    { schema: PROPOSAL, phase: 'Propose', label: `council:${String(L.lens).split('-')[0]}`, ...(agentType ? { agentType } : {}) })
)).then(rs => rs.filter(Boolean))

phase('Synthesize')
const synthesis = await agent(
  `You are the COUNCIL CHAIR deciding: ${topic}. ${synthInstruction}\n\n${ctx}\n\nPROPOSALS:\n${JSON.stringify(proposals).slice(0, 16000)}\n\nReturn ONLY the structured synthesis object (recommended_architecture, migration_phases, retire_list, open_questions, summary).`,
  { schema: SYNTH, phase: 'Synthesize', label: 'council-chair', ...(agentType ? { agentType } : {}) })

log(`design-council "${topic}": ${maps.length} maps, ${proposals.length} proposals, 1 synthesis`)
return { topic, maps, proposals, synthesis }
