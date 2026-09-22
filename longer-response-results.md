Later update: the character harness produced a complete 29-character sentence with one capitalization error. See `sentence-goal-results.md`. The report below preserves the earlier failed experiments and their conclusions at that time.

# Longer-response experiments

Longer responses remain incoherent. Only character-by-character modes remain in the active harness.

## Critique

- Greedy character choices turn an early error into a permanent prefix because deletion is prohibited.
- Showing complete prefixes was enough for the earlier Paris answer, but not for sentence generation.
- Routing masks prevent invalid spacing but cannot repair meaning or spelling.
- Independent Noul scores avoid some repeated-letter choices, but are not calibrated next-character probabilities. They also stop words prematurely.
- A separate word-completeness gate reduced premature endings but rejected some valid boundaries and still produced incoherent strings.
- Whole-word selection was explored before the character-only clarification and then discarded. It is not counted as a solution.

## Retained changes

The runnable harness emits at most one character per action, retains explicit space/word/punctuation/DONE routing, and has no delete action. Added boundary masks, repeated-word stopping, cancellation flags, unknown-request-outcome reporting, and a hard append invariant. Fourteen local tests passed. The original staged prefix method remains the default; the scored method is experimental.

## Raw generation outcomes

| Trace | Mode | Output | Stop |
|---|---|---|---|
| improve-01-explanation.jsonl | lexicon | `"because the is a "` | error |
| improve-01-sentence.jsonl | lexicon | `"The is is is is "` | error |
| improve-07-explanation.jsonl | scored | `"a fo X "` | cancelled |
| improve-07-sentence.jsonl | scored | `"P p r "` | cancelled |
| improve-08-explanation.jsonl | scored | `"TherewisnonXsYswyP"` | cancelled |
| improve-08-sentence.jsonl | scored | `"PrsEnnmCrTonFaPricO"` | cancelled |
| longer-01-explanation.jsonl | word | `"AAAAAAAAAAAA"` | repetition |
| longer-01-paragraph.jsonl | word | `"Ran            "` | repetition |
| longer-01-sentence.jsonl | word | `"AAAAAAAAAAAA"` | repetition |
| longer-02-explanation.jsonl | word | `"A AAAAAAAAAAAA"` | repetition |
| longer-02-paragraph.jsonl | word | `"Ran R A R RR RRR RRR RRR RRRRRRR RRR"` | error |
| longer-02-sentence.jsonl | word | `"A AAAAAAAAAAAA"` | repetition |
| longer-03-multiword-capital.jsonl | word | `"CABA"` | done |
| longer-03-multiword-name.jsonl | word | `"JaNe JaNe AAAAAAAAAAAA"` | repetition |
| longer-04-plain-explanation.jsonl | word | `"A AAAAAAAAAAAA"` | repetition |

Diagnostics and partial attempts are also included in the JSON report and raw files.

Recorded responses: 302; question judgments: 3582; reported cost for recorded responses: $0.00. Unknown failed/in-flight outcomes are excluded from those totals.

No claimed full-sentence success. The prior short-answer Paris result remains the only France success recorded by the character harness.
