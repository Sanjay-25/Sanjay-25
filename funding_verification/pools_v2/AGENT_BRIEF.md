# Brief: assign YC companies to Fast Code AI outreach pools

You are assigning funded YC companies to outreach pools for Fast Code AI, an applied ML lab. Getting the fit right matters more than speed: these assignments decide which proof point each founder sees in a cold message.

## Input
`/home/user/Sanjay-25/funding_verification/pools_v2/todo_batchN.csv` with columns: key, company, batch, website, one_liner, description (YC long description), tags, industry, latest_round_date, total_funding_usd, ml_jobs.

## Fast Code AI proof points (the only ones allowed)
- P2 ADAS / Physical AI: vision-language models, perception stacks, vision models, VLAs, world models. (Bosch, Mercedes-Benz MBUX; CausalDriveBench, NeurIPS.)
- P3 Agent quality, evals and coding agents: evaluation harnesses and quality systems for AI agents, agentic PR review. (ThoughtSpot, Entelligence, Tattvam AI.)
- P4 RAG / retrieval: hybrid retrieval over large enterprise and legal document sets. (ThoughtSpot, MIAI.)
- P5 Legal AI: AI systems built with a law firm. (MIAI.)
- P6 Search and optimization agents: search-based LLM agents that optimize against slow, expensive simulators (chip timing closure). (Tattvam AI.)
- P7 Industrial diagrams: digitizing engineering and process diagrams (P&IDs, PFDs) for industrial enterprises. (Saudi Aramco.)
- P1 Voice AI is NOT used. Companies whose core product is voice AI or speech (voice agents, voice models, dictation, call-answering AI, voice recorders) go to pool VOICE_EXCLUDED.

## Pools (use exactly these pool_id values)
| pool_id | proof points | who belongs |
|---|---|---|
| PHYS | P2 | Core problem is perception or vision-language modelling: robots, drones, autonomy, camera/video understanding, medical imaging foundation models, on-device vision. |
| PHYS_EVAL | P2 + P3 | Training data, evaluation or benchmarks for robots / physical AI / perception models. |
| AGENT_EVAL | P3 | Building AI agents (horizontal or vertical) where reliability, evaluation and QA of the agent is the hard engineering problem; agent platforms and frameworks; model reliability; evals/RL environments for LLMs. |
| CODE | P3 (coding facet) | Coding agents, AI code review, AI testing/QA of software, agents that write/modify/operate code or CI, AI app builders. |
| RAG | P4 | Core problem is retrieval or extraction over large document sets (contracts, filings, clinical records, regulations, data rooms, claims, knowledge bases), or retrieval infrastructure (search, rerankers, document parsing, context layers). |
| LEGAL | P4 + P5 | Legal AI: law firms, litigation, patents/IP, contracts for legal teams, immigration, legal research. |
| SEARCH | P6 | Search/optimization agents against expensive evaluators: GPU kernel or compiler optimization, materials/molecule discovery against simulation, chip/EDA design, CAD/engineering design optimization, benchmark hill-climbing. |
| DIAGRAM | P7 | Reading or digitizing engineering drawings, blueprints, schematics, P&IDs, maps/plans as structured data. |
| GENERIC | none | No proof point is the same kind of technical problem (fintech rails, marketplaces, consumer apps, hardware/energy/space, biotech wet lab, services, generic SaaS, etc.). |
| VOICE_EXCLUDED | none | Voice AI / speech products (see above). |

## How to decide
1. Write the company's core technical problem in one plain line: what is hard to build in its product, not its sector label.
2. A proof point applies only if a technical founder there would see it as the same kind of problem. Test: "Would the engineering work in the proof point transfer directly to their product?" Industry or buzzword overlap does not count. An "AI for X" company is not automatically AGENT_EVAL: put it there only if agent reliability/evaluation is genuinely central. If the company's hard problem is reading documents, it is RAG.
3. Pick the single best pool. Record one other applicable proof point in secondary_proof (e.g. "P4") if real, else blank.
4. fit_strength: Strong (same technical problem), Moderate (clearly adjacent), None (GENERIC and VOICE_EXCLUDED only).
5. If the description is too thin to judge, you may WebFetch the company homepage (at most ~25 fetches per batch, only for genuinely unclear cases). Do not guess.

## Text rules (apply to core_problem, reason, opener_hint)
- No em dashes (—). No "not X, but Y" framing. No lists of three used for rhythm. Plain words.
- core_problem: one plain line, no jargon.
- reason: one line, e.g. "clinical notes over thousands of PDFs = retrieval problem -> P4". For GENERIC say briefly why no proof point fits.
- opener_hint: one sentence naming a specific technical problem in THEIR product, stated with expert confidence. Not a question. No "curious", no filler lead-ins, no textbook definitions. Max ~20 words. Must not contain the "|" character.
- Never invent facts about the company beyond its description/homepage.

## Output
Write `/home/user/Sanjay-25/funding_verification/pools_v2/todo_batchN_assignments.psv`, one line per input company, pipe-separated, no header, exactly 8 fields:
`key|pool_id|secondary_proof|fit_strength|runner_up_pool|core_problem|reason|opener_hint`
runner_up_pool is the next best pool_id or blank. Every input row must appear exactly once. Use Python to write the file and then validate it (8 fields per line, all keys present, valid pool_id, no em dashes, no "|" inside fields).

## Calibration examples (from earlier hand-made assignments; same format, older pool list)
See `/home/user/Sanjay-25/funding_verification/pools_v2/calibration_examples.psv`.

## Final reply
Only: counts per pool_id, number of homepage fetches, and up to 10 companies you were genuinely unsure about with their two candidate pools.
