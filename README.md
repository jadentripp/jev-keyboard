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

`--paragraph` targets at least three sentences within 75–250 characters, excludes newlines, and increases the time limit to two hours. Reaching a limit reports an incomplete run. Explicit HTTP 503, 504, and 529 responses get up to six retries with backoff. Network failures with unknown outcomes stop the run; reported cost covers successful responses only.

## How it works

1. Jev chooses a word, space, or punctuation. It cannot choose END during speculative lookahead.
2. Within a word, it scores possible next letters for spelling and relevance, alongside the option to finish the word. Word endings are checked for grammatical agreement and a subject within the current sentence. Paragraph mode asks for short subject–verb sentences.
3. It explores up to six characters ahead, retains alternatives for each possible next action, and commits only the first action. Internal word and punctuation transitions do not consume the lookahead budget. It reconsiders the continuation at every step and caches judgments about previously explored states.

At the actual committed word or sentence boundary, Jev chooses STOP or CONTINUE and separately scores factual completeness, requested format, and a finished word boundary. It stops only on STOP with scores at least .60, .70, and .90 respectively. These thresholds were calibrated on eight previously generated prefixes and need broader testing. In paragraph mode, Jev checks whether each sentence is complete and grammatical before choosing its ending punctuation. Sentence starts use uppercase letters or digits. All model judgments can be wrong.

No example words, dictionary, supplied answer, deletion, or whole-word replay. The keyboard includes uppercase and lowercase letters, digits, ASCII punctuation, and newline.

## Result and limitations

The paragraph run produced five short sentences:

> It is calm. A swan stands. Shy swans swim. We watch swans. We walk around.

The exact task prompt, including the harness's paragraph instruction, was:

> Describe a calm day in one paragraph using short, common words and complete sentences. Use short subject-verb sentences. Put the subject before its finite verb.

This earlier run committed 75 characters (including a trailing space), then selected END. It took 5,247 successful requests and 13 minutes, with 221 HTTP errors. Successful responses reported $0 in cost; failed responses did not supply billing metadata. The recorded trace verifies that every visible change appended exactly one character, with no edits or seeded text.

With the revised thresholds, the recorded STOP scores accept the earlier paragraph **before** its trailing space and the prior arithmetic answer `So it is 4.` **before** a malformed continuation. They also accept `Rome`, `Pacific`, and `1`, while rejecting the committed prefixes `Pac`, `A atl i.`, and `So it is 4..isn't?`. These are eight retrospective probes on model-generated text, including the cases used to choose the thresholds; they are not a measured success rate on new questions.

The prose is basic, and other prompts produced spelling and grammar errors. A separate speculative beam decoder answered three of ten diverse development questions correctly, all short word or number answers; none of seven sentence attempts finished well. Gateway HTTP 503 interrupted several trials. Neither decoder has demonstrated reliable sentence or paragraph answers to arbitrary questions. The earlier sentence milestone was `Paris is a capital of france.`

[Recorded result and full experiment history](https://github.com/jadentripp/jev-keyboard/tree/b222ea69cfeec4f11bcc2bfba1acf21d5047a8c2) remain in Git history.

## Tests

```bash
python -m unittest -q
```

Tests run locally without API calls. They check character-only output, discarded lookahead, completion boundaries, response validation, and request limits.
