# Jev keyboard

A small experiment that makes [Jev](https://typesafe.ai) write one character at a time through Vercel AI Gateway. One Python file, using Requests to reuse connections.

## Run

Requires Python 3.10+ and an AI Gateway key. The script prompts for the key, or reads `AI_GATEWAY_API_KEY`.

```bash
python -m pip install requests
python jev_keyboard.py "What is the capital of France? Answer in a complete sentence."
python jev_keyboard.py "Describe a calm day in one paragraph using short, common words and complete sentences." --paragraph
```

Characters stream to the terminal as they are committed. Defaults: a 25–50 character target, a $0.10 reported-cost limit, and 30 minutes. Change the limits with `--min-chars`, `--max-chars`, and `--max-cost`. A verified complete answer can stop before the target minimum; the maximum is a hard limit. In-flight requests can exceed the cost or time limit.

`--paragraph` defaults to 75–250 characters, excludes newlines, and increases the time limit to two hours. Jev chooses when each sentence ends and checks whether the answer meets the requested form; there is no fixed sentence count. Reaching a limit reports an incomplete run. Explicit HTTP 503, 504, and 529 responses get up to six retries with backoff. Network failures with unknown outcomes stop the run; reported cost covers successful responses only.

## Frozen prefix-beam experiment

`beam_prefix_viable.py` is a separate experimental one-character decoder. It ranks speculative continuations softly, asks Jev whether prefixes can still become correct by appending, and checks spelling, grammar, factual content, and STOP at permanent character boundaries. Every visible character gets a fresh Jev request at its exact prior prefix. It uses no supplied answer words, example spellings, deletion, or word replay.

```bash
python beam_prefix_viable.py "What causes tides? Answer in one sentence." --trace tides.jsonl
```

The script reads `AI_GATEWAY_API_KEY` and saves the complete request/response ledger plus a copy of its exact source. The default limits are 240 characters, 1,800 successful requests, and 25 minutes; adjust them with `--length`, `--limit`, and `--max-seconds`. It may terminate early when Jev finds no safe next character, or choose STOP when the answer is complete. A limit or HTTP error does not count as a successful answer.

The frozen source SHA-256 is `d01b781c6e403db80846c59c846de5ae7c2da10b1749d6f5d300becf2836eb91`. In a fixed [ten-question suite](experiments/BEAM-RESULTS.md), it correctly stopped on one numeric question; eight other outputs failed content or format, and one trial lost its runner before a terminal summary. No explanatory sentence or paragraph passed. The [suite plan](experiments/beam-ten-suite-v1.json) and [result ledger](experiments/beam-ten-results-v1.jsonl) are included. The raw traces and independent audits are supplied separately in the experiment archive.

## How it works

1. Jev chooses a word, space, or punctuation. It cannot choose END during speculative lookahead.
2. Within a word, it scores possible next letters for spelling and relevance, alongside the option to finish the word. Word endings are checked for grammatical agreement and a subject within the current sentence. Paragraph mode asks for short subject–verb sentences.
3. It explores up to six characters ahead, retains alternatives for each possible next action, and commits only the first action. Internal word and punctuation transitions do not consume the lookahead budget. It reconsiders the continuation at every step and caches judgments about previously explored states.

At the actual committed word or sentence boundary, Jev chooses STOP or CONTINUE and separately scores factual completeness, requested format, and a finished word boundary. It stops only on STOP with scores at least .60, .70, and .90 respectively. These thresholds were calibrated on eight previously generated prefixes and need broader testing. Explicit requests for a number of sentences prevent an early STOP. In paragraph mode, syntax checks make a sentence ending eligible; Jev then chooses whether to end the sentence based on the question and requested form. Sentence starts use uppercase letters or digits. All model judgments can be wrong.

No example words, dictionary, supplied answer, deletion, or whole-word replay. The keyboard includes uppercase and lowercase letters, digits, ASCII punctuation, and newline.

## Result and limitations

The paragraph run produced five short sentences:

> It is calm. A swan stands. Shy swans swim. We watch swans. We walk around.

The exact task prompt, including the harness's paragraph instruction, was:

> Describe a calm day in one paragraph using short, common words and complete sentences. Use short subject-verb sentences. Put the subject before its finite verb.

This earlier run committed 75 characters (including a trailing space), then selected END. It took 5,247 successful requests and 13 minutes, with 221 HTTP errors. Successful responses reported $0 in cost; failed responses did not supply billing metadata. The recorded trace verifies that every visible change appended exactly one character, with no edits or seeded text.

With the revised thresholds, the recorded STOP scores accept the earlier paragraph **before** its trailing space and the prior arithmetic answer `So it is 4.` **before** a malformed continuation. They also accept `Rome`, `Pacific`, and `1`, while rejecting the committed prefixes `Pac`, `A atl i.`, and `So it is 4..isn't?`. These are eight retrospective probes on model-generated text, including the cases used to choose the thresholds; they are not a measured success rate on new questions.

The prose is basic, and other prompts produced spelling and grammar errors. A separate speculative beam decoder answered three of ten diverse development questions correctly, all short word or number answers; none of seven sentence attempts finished well. Gateway HTTP 503 interrupted several trials. Neither decoder has demonstrated reliable sentence or paragraph answers to arbitrary questions. The earlier sentence milestone was `Paris is a capital of france.`

A frozen second beam variant was then tested without changing its source between questions. It selected STOP for `6` in 2 requests and `Heart` in 30. Its three longer attempts failed: `As stated` after 69 requests, `Aaaaaa` after 51, and `They are a thundr` after 128. The `Aaaaaa` run was stopped by the operator after the prefix became irreparable; it was not a model-selected END. The public keyboard itself was rerun on `What is 2 + 2? Answer in a complete sentence.` and drifted to `See answeis`; that run was also stopped as incomplete after 1,108 successful requests. Its character ledger and source hash were audited. The revised STOP check can prevent extra text after a valid answer, but it cannot repair bad character choices.

More live tests exposed that problem. A one-letter boundary adjustment still failed the thermometer task at `A aaaa` (49 requests). Two new sentence decoders ended at `is because i` (340 requests) and `the seas` (130 requests); a third reached `the par ` (224 requests). Two trials with the public paragraph source ended at `Rain falls ` (646 requests, interrupted) and `An evaporating sea raise ` (1,552 requests, operator stopped). None was a completed explanation or paragraph. A separate beam that found a complete word and replayed its letters was excluded because it violated the one-character decision rule.

[Recorded result and full experiment history](https://github.com/jadentripp/jev-keyboard/tree/b222ea69cfeec4f11bcc2bfba1acf21d5047a8c2) remain in Git history.

## Tests

```bash
python -m unittest -q
```

Tests run locally without API calls. They check character-only output, discarded lookahead, completion boundaries, response validation, and request limits.
