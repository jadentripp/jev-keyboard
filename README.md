# Jev character keyboard

Jev reached the requested sentence-length milestone with this exact output:

> Paris is a capital of france.

**29 characters, generated individually.** The sentence is complete and factually correct; the country name has a capitalization error. The trace preserves it verbatim. This is one sentence milestone, not evidence of reliable answers to arbitrary questions.

See `sentence-goal-results.md` and `sentence-goal-results.json` for the result, known limitation, failed attempts, and accounting. The complete generation history is `continue-20-sentence.jsonl` followed by `continue-21-sentence.jsonl`; the second segment resumes the first segment's entire unchanged prefix.

## Run

Python 3.10+, standard library only:

```bash
python search_keyboard.py "What is the capital of France? Answer in a complete sentence."
python verify_character_trace.py continue-21-sentence.jsonl
python -m unittest -q test_keyboard.py
```

Provide the key at the hidden prompt or through `AI_GATEWAY_API_KEY`. Keys are not written to source or traces.

## Character generation

1. Route to WORD, SPACE, PUNCTUATION, or DONE.
2. In WORD, Jev judges whether the current word is complete. Otherwise it selects lowercase, uppercase, or digit, then selects one character from that group.
3. For uncertain letters, Jev tries its four highest-probability characters and predicts one additional action for each. Jev ranks those model-generated continuations. Commit only the first character and discard the speculative future action.
4. Punctuation is selected separately. Spaces and punctuation are actual model-selected character additions.

Every committed action appends zero or one character; the harness asserts that existing text remains unchanged. END_WORD changes stages without inserting a word. There are no example words, dictionaries, supplied answers, second language models, delete keys, or whole-word replay. The full ASCII QWERTY alphabet, both cases, digits, punctuation, and newline remain available through the stages.

Routing sees the length requirement. DONE is unavailable before the minimum length and before terminal punctuation. The default target is 25–50 characters, configurable with `--min-chars` and `--max-chars`. A length limit, a completed word, and model-selected DONE are structural events, not proof of correctness. Semantic quality is reviewed separately.

`--resume-trace PATH` reconstructs the entire recorded prefix, validates continuity, and resumes with the same task. It cannot trim or replace prior characters. `--stop-file PATH` cancels between requests when that file appears.

Search defaults: 250 actions, 30 minutes, $0.10 of recorded Gateway cost, four speculative branches, and a 0.55 search threshold. Limits are checked between requests. In-flight requests can exceed time or cost thresholds; interrupted or uncertain requests are flagged. Successful-run accounting includes both resumed segments and all recorded speculation.

## Verification and limitations

The audit follows the trace back to an empty draft and verifies all 29 character additions. The successful run chain used 139 requests over 662.42 seconds and reported $0 Gateway cost. Earlier failures and separate diagnostics are excluded from those totals. Sixteen local tests passed; these tests check harness behavior and do not demonstrate language quality.

Proper-noun capitalization is still wrong in the recorded sentence. Other questions failed. Greedy word boundaries, limited lookahead, and model overconfidence can still produce permanent spelling or grammatical errors because deletion is forbidden.

`jev_keyboard.py` preserves the original baseline modes. Its default remains `word`; the latest sentence experiment is the separate `search_keyboard.py` entry point. `planned_keyboard.py`, `character_scoring.py`, and `character_probes.py` preserve earlier failed experiments. The optional blended boundary judgment in the search source is experimental and was not used in the measured sentence run.

Historical dictionary-assisted traces are retained as explicitly excluded evidence. The discarded word-list and hint-based generators are absent from the runnable archive. No active source imports a dictionary package.

## Public trace redaction

Published traces omit provider generation identifiers and routing metadata. Prompts, choices, probabilities, committed characters, token usage, costs, and audit continuity are retained.
