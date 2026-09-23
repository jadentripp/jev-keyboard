# Frozen prefix beam: live Jev evaluation

## Method

The frozen source is `beam-experiments/beam-prefix-viable-frozen.py`, SHA-256
`d01b781c6e403db80846c59c846de5ae7c2da10b1749d6f5d300becf2836eb91`.
It imports `jev_keyboard.py`, SHA-256
`9dcb2f55aa7a78c4755f1bffa28ef656c421c02052b28f256653671af9959cfc`.
The suite was declared in `beam-ten-suite-v1.json` before its first request:
ten questions in fixed order, empty initial answer, beam width 6, depth 3,
and per-case character, successful-request, and elapsed-time limits. There
are no answer examples, supplied target words, seeded prefixes, deletions, or
replayed word fragments. Every visible character is committed after a fresh
Jev request at the exact prior visible prefix. A separate Jev Choice proposes
STOP; factual, requested-form, and finished-word scores guard that decision.

Speculative beam paths receive *soft* Jev rankings and append-only prefix
viability scores. Spelling, word order, and factual checks may veto a permanent
space or sentence mark. A letter can be refused when it joins an already
complete word into a nonword or creates four identical letters in a row.
The article grammar check only applies when a completed article would be
immediately followed by an incompatible completed word. These checks cannot
repair a bad prefix once it has been committed.

## Development explanations from an empty answer

The following runs were used to adjust the decoder and are **not** part of
the ten-question fixed suite. Every trace has a source snapshot, all requests,
all HTTP errors, one-character commits, rejected STOP votes, and a terminal
summary. `audit_beam_prefix.py` checks the ledger independently.

| Revision, task | Exact final text | Successful requests | HTTP errors | End |
| --- | --- | ---: | ---: | --- |
| v5, evaporation | `Evaporation as an is any` | 146 | 14 | No safe append |
| v5, shadows | `Shadows are shown as shadows` | 170 | 24 | No safe append |
| v5, tides | `The masses are assess` | 129 | 17 | No viable append-only completion |
| v6, evaporation | `Evaporation as` | 86 | 10 | Overstrict grammar boundary |
| v7, evaporation | `Evaporation is an evaporation` | 176 | 23 | Self-definition, no safe append |
| v7, shadows | `Shadows are shaded by themes` | 170 | 17 | No safe append |
| v8, tides | `The masses at an awwayaaaaawayaaaaawayaaaaaaaawayaaaaawayaaaaaaaaaaaaaaaaaa` | 450 | 79 | Request cap |
| **v9 frozen**, tides | `The masses are at` | 104 | 16 | No safe append |
| **v9 frozen**, evaporation | `Evaporation as an ismsaaaz` | 159 | 14 | No viable append-only completion |

The targeted word-order diagnostic scored the invalid `an is` word closure
.10 on a precise preceding-word requirement, but a broad adjacency question
scored it .69. The precise question also scored the repairable phrase
`Evaporation as` .18, so v6's generic .25 hard cutoff was too broad. The
frozen version restricts that hard check to a closed article. In v9 at
`Evaporation as an is`, the proposed space scored .08 and was rejected;
Jev extended `is` into a nonword instead. The v8 tides run illustrates the
opposite error: Jev scored fourth repeated `a` choices as .51–.57 viable,
so v9 uses a narrow deterministic spelling guard. Neither improvement yielded
a finished explanation. The 15 focused offline guard tests pass, but they
only check behavior under controlled model responses.

## Ten-question fixed suite

| Question | Exact visible output | Calls | HTTP errors | Terminal reason | Manual judgment |
| --- | --- | ---: | ---: | --- | --- |
| coins_added | `5` | 3 | 0 | Jev STOP | Pass: correct number; Jev STOP |
| iron_symbol | `Ferrum aaazaaazaaazaaazaaazaaaza` | 189 | 20 | character cap | Fail: Latin name then nonword; no STOP |
| france_japan_capitals | `Capitals isasasasaaa` | 122 | 21 | no safe append | Fail: neither capital; no STOP |
| mercury_or_neptune | `Mercury, as fareaaasaaasaaasaaasaaasaaasaaasaaasaaasaaasaaasaaasaaasaaasaaasaaasaaasaaasaaasaaasaaasaaasaaasaaa` | 672 | 206 | HTTP 503 after retries | Fail: correct planet name, then malformed text; 503 end |
| triangle_definition | `An a trianglesaaasaaasaaa` | 148 | 13 | runner disappeared | Interrupted: malformed partial text; no terminal summary |
| thermometer_units | `The measures` | 74 | 7 | no safe append | Fail: measurement and unit missing; no sentences |
| weather_vs_climate | `Weather whasaasaaawaataaawa` | 165 | 17 | no viable continuation | Fail: no distinction or complete sentence |
| day_night | `As a cauase assesssaaasaaawaaawaaawaaawaaawaaawaaawaaawaaawawaaawaaawaaawaaawaaawaaawaaawaaawaaawaaa` | 606 | 196 | HTTP 503 after retries | Fail: false spelling acceptance and repeated tail; 503 end |
| unanswerable_lottery | `Ask aaazaa` | 63 | 9 | no viable continuation | Fail: no uncertainty statement |
| park_paragraph | `People are walking. And with an aww` | 214 | 64 | no safe append | Fail: one relevant sentence, then unfinished phrase |

Exactly **1 of 10 first attempts** met the question, requested form, and Jev-selected STOP: the coin answer `5`. Eight other attempts failed on the visible content or ending; the triangle trial was interrupted by the runner and is counted as an unsuccessful first attempt, not as a model-selected end. Of the eight questions asking for a sentence, multiple sentences, or a paragraph, none yielded an accepted response; seven have normal or transport terminal summaries and the triangle trace does not. There were 2,256 successful Jev requests, 553 logged HTTP/network errors, and 373 visible one-character commits across the ten attempts. Those totals include unsuccessful work.

At the exact prefix `Mercury`, Jev's STOP Choice selected STOP and the fact score was .91, but the requested-sentence form score was .27, so the harness correctly refused to end on a one-word response. It then produced malformed text. At `Ferrum`, Jev also selected STOP, but the fact score was .29; the guard refused that questionable answer and the continuation degenerated. These are sound STOP vetoes that did not repair generation. Conversely, at `As a cauase` in the day/night run Jev rated the misspelled closed word .83, above the .68 spelling gate. That false positive made the error permanent.

The triangle worker session disappeared after `An a trianglesaaasaaasaaa`. Its trace has 148 successful requests, 13 HTTP errors, 25 audited one-character commits, and no terminal summary. The recovery ledger marks `runner_disappeared` and keeps that original trace unchanged. The remaining cases were run in their declared order under the exact frozen source and limits; there was no silent retry, answer seeding, or case substitution. The `HTTP 503 after retries` endings for the planet and day/night cases are transport interruptions, and both outputs were already invalid before the transport stopped them.

**Failure modes:** Jev can give high viability or spelling confidence to nonwords; finite lookahead chooses topic repetition and sentence fragments; append-only output cannot repair an accepted wrong word; repeated patterns evade a four-identical-letter guard; strict STOP checks prevent premature endings yet expose the weak continuation policy; gateway errors can consume time and interrupt attempts. These ten trials do not support a claim that the harness reliably answers arbitrary questions or writes paragraphs. The sole success is a short numeric response.

## How to reproduce the audit

Run `python eval-design/verify_beam_ten.py` from the workspace root after the
suite completes. It checks the exact frozen source and keyboard hashes,
unchanged plan hash, case order and count, trace hashes, every one-character
append and fresh Jev request, STOP evidence, recorded text, and caps. The
complete JSONL traces and source snapshots are in `beam-experiments/`.
Successful gateway responses reported $0 cost in these runs; failed responses
did not include billing metadata. Do not infer a zero final bill from that
reported field.
