# Jev keyboard

A small experiment that makes [Jev](https://typesafe.ai) write a sentence one character at a time through Vercel AI Gateway. One Python file, no dependencies.

## Run

Requires Python 3.10+ and an AI Gateway key. The script prompts for the key, or reads `AI_GATEWAY_API_KEY`.

```bash
python jev_keyboard.py "What is the capital of France? Answer in a complete sentence."
```

Characters stream to the terminal as they are committed. Defaults: 25–50 characters, a $0.10 reported-cost limit, and 30 minutes. Change the limits with `--min-chars`, `--max-chars`, and `--max-cost`. In-flight requests can exceed the cost or time limit.

## How it works

1. Jev chooses word, space, punctuation, or END.
2. Within a word, it decides whether the word is finished, then selects a character's case and the character.
3. For uncertain letters, it compares four short continuations made from its own predictions. Only one character is committed; the speculative next action is discarded.

No example words, dictionary, supplied answer, deletion, or whole-word replay. The keyboard includes uppercase and lowercase letters, digits, ASCII punctuation, and newline.

## Result and limitations

The earlier harness produced **29 characters**:

> Paris is a capital of france.

That run used 139 requests over about 11 minutes. The lowercase country name is an error. Other questions failed; this is an experiment, not a reliable text generator. END means the model stopped, not that its answer was independently verified. This simplified version retains the character-selection approach; the timing above is from the earlier implementation.

[Recorded result and full experiment history](https://github.com/jadentripp/jev-keyboard/tree/b222ea69cfeec4f11bcc2bfba1acf21d5047a8c2) remain in Git history.

## Tests

```bash
python -m unittest -q
```

Tests run locally without API calls. They check character-only output, discarded lookahead, completion boundaries, response validation, and request limits.

The refactor also reproduced all 20 actions in the recorded final continuation, matching the original requests and actions without live API calls.
