"""Generate a sentence with Jev, one character at a time. Standard library only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import getpass
import json
import math
import os
import string
import sys
from threading import Lock
import time
import urllib.error
import urllib.request

ENDPOINT = "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
LETTERS = "qwertyuiopasdfghjklzxcvbnm"
CHARACTERS = LETTERS + LETTERS.upper() + string.digits
PUNCTUATION = "`~!@#$%^&*()-_=+[{]}\\|;:'\",<.>/?"


def probability(value):
    if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Invalid model probability")
    return value


def choice(answer, options):
    probabilities = answer["probabilities"]
    selected = answer["choice"]
    if answer.get("type") != "choice" or set(probabilities) != set(options) or selected not in options:
        raise ValueError("Invalid model choice")
    values = [probability(p) for p in probabilities.values()]
    if not math.isclose(sum(values), 1, abs_tol=.02) or probabilities[selected] < max(values) - 1e-6:
        raise ValueError("Invalid choice distribution")
    return selected, probabilities


class Jev:
    def __init__(self, key, max_cost=.10):
        self.key, self.max_cost = key, max_cost
        self.cost, self.calls = 0., 0
        self.deadline = time.monotonic() + 1800
        self.lock = Lock()

    def ask(self, state, questions):
        with self.lock:
            if self.cost >= self.max_cost or time.monotonic() >= self.deadline:
                raise RuntimeError("Cost or time limit reached")
        body = {"model": "typesafe-ai/jev", "state": state, "questions": questions}
        request = urllib.request.Request(ENDPOINT, json.dumps(body).encode(), headers={
            "Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                data = json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Gateway returned HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError("Network failure; request outcome unknown. No automatic retry.") from None
        cost = float(data["provider_metadata"]["gateway"]["cost"])
        if not math.isfinite(cost) or cost < 0:
            raise ValueError("Invalid cost metadata")
        with self.lock:
            self.calls += 1
            self.cost += cost
        return data["answers"]

    def choose(self, state, prompt, options):
        answer = self.ask(state, {"next_key": {
            "type": "choice", "instructions": prompt, "criteria": options}})["next_key"]
        return choice(answer, options)


@dataclass(frozen=True)
class Keyboard:
    text: str = ""
    word: str = ""
    stage: str = "route"

    @property
    def draft(self):
        return self.text + self.word

    def apply(self, action):
        if self.stage == "word":
            if action == "END_WORD" and self.word:
                return Keyboard(self.draft)
            if len(action) == 1 and action in CHARACTERS:
                return replace(self, word=self.word + action)
        elif self.stage == "punctuation":
            if action == "ENTER" or (len(action) == 1 and action in PUNCTUATION):
                return Keyboard(self.text + ("\n" if action == "ENTER" else action))
        elif self.stage == "route":
            if action == "SPACE":
                return replace(self, text=self.text + " ")
            if action in ("WORD", "PUNCTUATION"):
                return replace(self, stage="word" if action == "WORD" else "punctuation")
            if action == "DONE":
                return self
        raise ValueError("Invalid keyboard action")


def decide(jev, task, board, min_chars, max_chars):
    if board.stage == "route":
        options = {"WORD": "Start spelling the next word.", "SPACE": "Append one space.",
                   "PUNCTUATION": "Select a punctuation character.",
                   "DONE": "The response fully satisfies the task, including its requested format."}
        if not board.text or board.text[-1].isspace():
            options.pop("SPACE")
        if board.text and board.text[-1].isalnum():
            options.pop("WORD")
        if len(board.text) < min_chars or not board.text.rstrip().rstrip('\"\u201d\u2019)').endswith((".", "!", "?")):
            options.pop("DONE")
        state = {"task": task, "text": board.text,
                 "length_requirement": {"min_characters": min_chars, "max_characters": max_chars}}
        return jev.choose(state, "Continue this sentence.", options)
    if board.stage == "punctuation":
        options = dict.fromkeys(PUNCTUATION)
        options["ENTER"] = "Newline"
        return jev.choose({"task": task, "text": board.text}, "Which punctuation mark?", options)

    state = {"answer_so_far": board.draft, "task": task}
    questions = {}
    if board.word:
        questions["word_complete"] = {"type": "noul", "instructions":
            "Is " + json.dumps(board.word) + " a fully spelled word in a correct answer to the task?"}
    kinds = {"lower": "Lowercase letter.", "upper": "Uppercase letter.", "digit": "Digit."}
    questions["letter_case"] = {"type": "choice",
        "instructions": "What kind of character comes next in the answer?", "criteria": kinds}
    answers = jev.ask(state, questions)
    if board.word and probability(answers["word_complete"]["noul"]) >= .4:
        return "END_WORD", {}
    kind, _ = choice(answers["letter_case"], kinds)
    matches = {"lower": str.islower, "upper": str.isupper, "digit": str.isdigit}[kind]
    characters = {f"key_{i}": char for i, char in enumerate(CHARACTERS) if matches(char)}
    options = {key: "An answer beginning with " + json.dumps(board.draft + char)
               for key, char in characters.items()}
    key, probabilities = jev.choose(state, task, options)
    return characters[key], {characters[k]: p for k, p in probabilities.items()}


def next_action(jev, task, board, min_chars, max_chars):
    action, probabilities = decide(jev, task, board, min_chars, max_chars)
    if board.stage != "word" or action == "END_WORD" or probabilities[action] >= .55:
        return action
    candidates = sorted(probabilities, key=probabilities.get, reverse=True)[:4]

    def preview(char):
        trial = board.apply(char)
        future, _ = decide(jev, task, trial, min_chars, max_chars)
        return trial.apply(future).draft

    with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
        previews = list(pool.map(preview, candidates))
    winner, _ = jev.choose({"task": task, "committed_prefix": board.draft},
        "Which continuation is most grammatical and relevant to the task?",
        {str(i): text for i, text in enumerate(previews)})
    # Commit only the first character; discard the speculative future action.
    return candidates[int(winner)]


def generate(jev, task, min_chars=25, max_chars=50, on_character=lambda char: None):
    if not 1 <= min_chars <= max_chars:
        raise ValueError("Require 1 <= min_chars <= max_chars")
    board = Keyboard()
    reason = "action_limit"
    for _ in range(250):
        if len(board.draft) >= max_chars and board.stage != "route":
            reason = "character_limit"
            break
        action = next_action(jev, task, board, min_chars, max_chars)
        if action == "DONE":
            return {"text": board.draft, "completed": True, "reason": "done"}
        updated = board.apply(action)
        if len(updated.draft) > max_chars:
            reason = "character_limit"
            break
        assert updated.draft.startswith(board.draft) and len(updated.draft) - len(board.draft) in (0, 1)
        appended = updated.draft[len(board.draft):]
        board = updated
        if appended:
            on_character(appended)
        if len(board.draft) >= 12 and len(set(board.draft[-12:])) == 1:
            reason = "repetition"
            break
    return {"text": board.draft, "completed": False, "reason": reason}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--min-chars", type=int, default=25)
    parser.add_argument("--max-chars", type=int, default=50)
    parser.add_argument("--max-cost", type=float, default=.10)
    args = parser.parse_args()
    if not 1 <= args.min_chars <= args.max_chars or not math.isfinite(args.max_cost) or args.max_cost <= 0:
        parser.error("Character limits and max cost must be positive and valid")
    key = os.environ.get("AI_GATEWAY_API_KEY") or getpass.getpass("AI Gateway key: ")
    jev = Jev(key, args.max_cost)
    try:
        result = generate(jev, args.question, args.min_chars, args.max_chars,
                          on_character=lambda char: print(char, end="", flush=True))
    except (RuntimeError, ValueError, KeyError, TypeError, KeyboardInterrupt) as error:
        print(f"\nStopped: {str(error) or 'interrupted'}", file=sys.stderr)
        return 1
    print()
    print(f"{result['reason']}; {jev.calls} requests; ${jev.cost:.6f} reported cost", file=sys.stderr)
    return 0 if result["completed"] else 1


if __name__ == "__main__":
    sys.exit(main())
