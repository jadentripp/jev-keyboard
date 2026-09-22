# Sentence-generation milestone

Jev produced the following exact output and selected DONE:

> Paris is a capital of france.

**29 characters: 23 letters, 5 spaces, and 1 period.** This is a complete, grammatical, factually correct sentence. It has one capitalization error: `france` should be capitalized. The recorded output has not been corrected.

The 25–50-character sentence milestone is reached. Perfect orthography and dependable generation across arbitrary questions are not established.

## Evidence

- Prompt: `What is the capital of France? Answer in a complete sentence.`
- Started empty in `continue-20-sentence.jsonl`, then resumed its entire unchanged prefix in `continue-21-sentence.jsonl`.
- 29 individual character additions; 43 total actions including stage changes and DONE.
- 139 recorded requests; 195 Choice/Noul questions; 662.42 seconds of active generation across both segments.
- Recorded Gateway cost: $0.00. These totals cover this run chain, including its speculative requests, and exclude earlier failed experiments and separate diagnostics.
- No dictionary, example words, supplied answer, second model, deletion, or whole-word replay.

## Harness

`search_keyboard.py` first chooses WORD, SPACE, PUNCTUATION, or DONE. Word spelling separates the word-boundary decision from character case and letter selection. The next-letter choices are mechanically constructed one-character extensions of the committed prefix.

When a letter prediction has probability below 0.55, Jev explores the top four characters, predicts one additional action for each branch, and ranks those continuations. Only the first character is committed. The future action is discarded, then predicted again at the next step. Every speculative text character also comes from Jev.

Before resuming, routing was changed to “Continue this sentence.” and given the 25–50-character requirement. Existing text was carried forward without modification. DONE is unavailable before 25 characters or before sentence-ending punctuation. Length and punctuation checks alone do not establish semantic quality.

The last-word boundary threshold in the measured run is 0.4. The optional blended spelling judgment is experimental and was not used in this run. A separate post-run capitalization diagnostic also chose lowercase; it was not used to edit the output.

## Reproduce and audit

```bash
python search_keyboard.py "What is the capital of France? Answer in a complete sentence."
python verify_character_trace.py continue-21-sentence.jsonl
```

The API key is read from the hidden prompt or `AI_GATEWAY_API_KEY` and is never saved in the trace. The code contains no question-specific words or answers. Exact outputs can vary.

## Other continuation attempts

| Trace | Output | Status |
|---|---|---|
| continue-03-explanation.jsonl | `"because the "` | Excluded: dictionary examples |
| continue-03-sentence.jsonl | `"The capital is Paris."` | Excluded: dictionary examples |
| continue-04-explanation.jsonl | `"because the difference is "` | Excluded: dictionary examples |
| continue-05-rain.jsonl | `"the The"` | Excluded: dictionary examples |
| continue-10-explanation.jsonl | `"Ice Icec Icc"` | no final summary |
| continue-10-sentence.jsonl | `"Aaa aaa aaa"` | no final summary |
| continue-13-france.jsonl | `"P a ris a aaaaaaaaaa"` | error |
| continue-13-ice.jsonl | `"I t a s a a"` | no final summary |
| continue-14-france.jsonl | `"Paris Aaaaaaaaaaaaa"` | repetition |
| continue-14-ice.jsonl | `"ItsAaaaaaaaaaaaa"` | repetition |
| continue-17-france.jsonl | `"Paris is a cAPa"` | error |
| continue-17-ice.jsonl | `"ice"` | done |
| continue-18-sentence.jsonl | `"Paris is an aaaaaa"` | error |
| continue-18-sky.jsonl | `"Ahahahaa"` | error |
| continue-20-open.jsonl | `"It is a ca"` | error |
| continue-20-sentence.jsonl | `"Paris is a capit"` | error |
| continue-20-sky.jsonl | `"Blue.aA"` | error |
| continue-21-open.jsonl | `"It is a cat a nan"` | error |
| continue-21-sentence.jsonl | `"Paris is a capital of france."` | Milestone, casing issue |

All failures and incomplete attempts remain visible. `completed` in a raw summary means the model selected DONE; it is not independently a success verdict.
