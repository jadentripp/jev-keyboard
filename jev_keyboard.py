"""Generate text with Jev, one character at a time."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from functools import lru_cache
import getpass
import json
import math
import os
from queue import LifoQueue
import re
import string
import sys
from threading import Lock
import time
import requests

ENDPOINT = "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
WORKERS = 8
LETTERS = "qwertyuiopasdfghjklzxcvbnm"
CHARACTERS = LETTERS + LETTERS.upper() + string.digits + "'"
PUNCTUATION = "`~!@#$%^&*()-_=+[{]}\\|;:'\",<.>/?"


def valid_word_char(word, char):
    if char == "'":
        return bool(word and word[-1].isalpha() and "'" not in word)
    return char in CHARACTERS


def valid_punctuation(text, mark):
    token = re.search(r"\S+$", text)
    return mark != "'" or not (token and "'" in token.group() and
                                  not token.group().startswith("'"))


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
    if not math.isclose(sum(values), 1, abs_tol=.02) or probabilities[selected] < max(values) - .02:
        raise ValueError("Invalid choice distribution")
    return selected, probabilities


class Jev:
    def __init__(self, key, max_cost=.10, paragraph=False):
        self.key, self.max_cost = key, max_cost
        self.cost, self.calls = 0., 0
        self.paragraph = paragraph
        self.deadline = time.monotonic() + (7200 if paragraph else 1800)
        self.lock = Lock()
        self.clients = LifoQueue()
        for _ in range(WORKERS):
            self.clients.put(requests.Session())

    def ask(self, state, questions):
        with self.lock:
            if self.cost >= self.max_cost or time.monotonic() >= self.deadline:
                raise RuntimeError("Cost or time limit reached")
        body = {"model": "typesafe-ai/jev", "state": state, "questions": questions}
        client = self.clients.get()
        try:
            for attempt in range(7):
                with self.lock:
                    if self.cost >= self.max_cost or time.monotonic() >= self.deadline:
                        raise RuntimeError("Cost or time limit reached")
                response = client.post(ENDPOINT, json=body, timeout=45,
                                       headers={"Authorization": "Bearer " + self.key})
                if response.status_code not in (503, 504, 529) or attempt == 6:
                    break
                time.sleep(min(2 ** attempt, 16))
            if not response.ok:
                raise RuntimeError(f"Gateway returned HTTP {response.status_code}")
            data = response.json()
        except requests.RequestException:
            raise RuntimeError("Network failure; request outcome unknown. No automatic retry.") from None
        finally:
            self.clients.put(client)
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
            if action == "END_WORD" and self.word and self.word[-1] != "'":
                return Keyboard(self.draft)
            if len(action) == 1 and valid_word_char(self.word, action):
                return replace(self, word=self.word + action)
        elif self.stage in ("punctuation", "ending"):
            if self.stage == "ending" and action not in (".", "!", "?"):
                raise ValueError("Invalid sentence ending")
            if action == "ENTER" or (len(action) == 1 and action in PUNCTUATION
                                     and valid_punctuation(self.text, action)):
                return Keyboard(self.text + ("\n" if action == "ENTER" else action))
        elif self.stage == "route":
            if action == "SPACE":
                return replace(self, text=self.text + " ")
            if action in ("WORD", "PUNCTUATION", "END_SENTENCE"):
                return replace(self, stage={"WORD": "word", "PUNCTUATION": "punctuation",
                                           "END_SENTENCE": "ending"}[action])
            if action == "DONE":
                return self
        raise ValueError("Invalid keyboard action")


@lru_cache(maxsize=8192)
def decide(jev, task, board, min_chars, max_chars):
    if board.stage == "route":
        paragraph = getattr(jev, "paragraph", False)
        sentence = re.split(r"(?<=[.!?])\s+", board.text)[-1]
        sentence_count = len(re.findall(r"[.!?](?=\s|$)", board.text))
        if paragraph and len(sentence.split()) >= 3 and board.text[-1].isalnum():
            checks = jev.ask({"sentence": sentence}, {
                "complete": {"type": "noul", "instructions": "Is the sentence a complete sentence, with a subject and a verb?"},
                "grammar": {"type": "noul", "instructions": "Is the sentence grammatically acceptable English?"}})
            complete = probability(checks["complete"]["noul"])
            grammar = probability(checks["grammar"]["noul"])
            if complete >= .9 and grammar >= .8:
                return "END_SENTENCE", {"END_SENTENCE": complete}
        options = {"WORD": "Start spelling the next word.", "SPACE": "Append one space.",
                   "PUNCTUATION": "Select a punctuation character."}
        if not board.text or board.text[-1].isspace():
            options.pop("SPACE")
        if paragraph and board.text.endswith((".", "!", "?")):
            options.pop("PUNCTUATION")
        if board.text and (board.text[-1].isalnum() or
                           (getattr(jev, "paragraph", False) and board.text[-1] in ".!?,;:")):
            options.pop("WORD")
        state = {"task": task, "text": board.text,
                 "length_requirement": {"min_characters": min_chars, "max_characters": max_chars}}
        if paragraph:
            state["completed_sentences"] = sentence_count
        prompt = "Continue this paragraph." if getattr(jev, "paragraph", False) else "Continue this sentence."
        return jev.choose(state, prompt, options)
    if board.stage in ("punctuation", "ending"):
        options = dict.fromkeys(".!?" if board.stage == "ending" else
                                (mark for mark in PUNCTUATION
                                 if valid_punctuation(board.text, mark)))
        if getattr(jev, "paragraph", False) and board.stage == "punctuation":
            for mark in ".!?":
                options.pop(mark)
        if board.stage != "ending" and not getattr(jev, "paragraph", False):
            options["ENTER"] = "Newline"
        prompt = "Which mark ends this sentence?" if board.stage == "ending" else "Which punctuation mark?"
        return jev.choose({"task": task, "text": board.text}, prompt, options)

    state = {"answer_so_far": board.draft, "completed_text": board.text,
             "unfinished_word": board.word, "task": task}
    sentence = re.split(r"(?<=[.!?])\s+", board.draft)[-1]
    questions = {}
    if board.word:
        questions["word_complete"] = {"type": "noul", "instructions":
            "Is " + json.dumps(board.word) + " a complete, correctly spelled word?"}
        questions["grammar"] = {"type": "noul", "instructions":
            "Do the words agree grammatically? The sentence may continue, but the last word is complete."}
        if getattr(jev, "paragraph", False):
            questions["subject"] = {"type": "noul", "instructions":
                "Does the sentence start with a subject or the beginning of a subject phrase?"}
    kinds = {"lower": "Lowercase letter.", "upper": "Uppercase letter.", "digit": "Digit."}
    if not board.word and (not board.text or re.search(r"[.!?]\s+$", board.text)):
        kinds.pop("lower")
    answers = jev.ask({"sentence_so_far": sentence, "unfinished_word": board.word}, questions) if questions else {}
    word_complete = min(probability(answers[k]["noul"]) for k in questions) if board.word else None
    kind, _ = jev.choose(state, "What kind of character comes next in the answer?", kinds)
    matches = {"lower": str.islower, "upper": str.isupper, "digit": str.isdigit}[kind]
    characters = {f"key_{i}": char for i, char in enumerate(CHARACTERS)
                  if (matches(char) or char == "'") and valid_word_char(board.word, char)}
    questions = {}
    for key, char in characters.items():
        prefix = json.dumps(board.word + char)
        questions[key] = {"type": "noul", "instructions":
            "Is " + json.dumps(sentence + char) +
            " a likely beginning of a grammatical sentence that adds useful information to the answer? The final word may be unfinished."}
        questions[key + "_spelling"] = {"type": "noul", "instructions":
            "Is " + prefix + " the beginning of a correctly spelled word?"}
    answers = jev.ask(state, questions)
    if set(answers) != set(questions) or any(answer.get("type") != "noul" for answer in answers.values()):
        raise ValueError("Invalid character scores")
    scores = {char: min(probability(answers[k]["noul"]) for k in (key, key + "_spelling"))
              for key, char in characters.items()}
    if word_complete is not None and board.word[-1] != "'":
        scores["END_WORD"] = word_complete
    return max(scores, key=scores.get), scores


def next_action(jev, task, board, min_chars, max_chars):
    action, probabilities = decide(jev, task, board, min_chars, max_chars)
    sentence_start = len(board.draft) - len(re.split(r"(?<=[.!?])\s+", board.draft)[-1])
    def proposals(scores, width):
        threshold = max(scores.values()) * .5
        return [key for key in sorted(scores, key=scores.get, reverse=True)[:width]
                if scores[key] >= threshold and key != "DONE"]
    roots = proposals(probabilities, 4 if board.stage == "word" else 2)
    if len(roots) == 1:
        return roots[0]
    def expand(item):
        root, trial, strength = item
        future, scores = decide(jev, task, trial, min_chars, max_chars)
        futures = proposals(scores, 4 if trial.stage == "word" else 2)
        results = []
        for action in futures:
            updated = trial.apply(action)
            score = min(strength, scores[action])
            # Internal stage changes do not consume a character of lookahead.
            if updated.draft == trial.draft and action != "DONE":
                results.extend(expand((root, updated, score)))
            else:
                results.append((root, updated, score))
        return results
    beam = []
    for root in roots:
        trial = board.apply(root)
        item = (root, trial, probabilities[root])
        beam.extend(expand(item) if trial.draft == board.draft else [item])
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for _ in range(5):
            previews = list(dict.fromkeys(item for group in pool.map(expand, beam) for item in group))
            best = max(strength for _, _, strength in previews)
            previews = [item for item in previews if item[2] >= best * .5]
            if len({root for root, _, _ in previews}) == 1:
                return previews[0][0]
            winner, scores = jev.choose({"task": task, "committed_prefix": board.draft},
                "Which continuation adds relevant information with correct spelling and grammar? The final word may be unfinished.",
                {str(i): json.dumps(trial.draft[sentence_start:])
                 for i, (_, trial, _) in enumerate(previews)})
            selected = previews[int(winner)][0]
            beam, counts = [], {}
            for key in sorted(scores, key=lambda key: (scores[key], previews[int(key)][2]), reverse=True):
                item = previews[int(key)]
                if counts.get(item[0], 0) < 2:
                    beam.append(item)
                    counts[item[0]] = counts.get(item[0], 0) + 1
    # Reconsider the continuation after committing only the first action.
    return selected


def answer_complete(jev, task, board):
    """Ask for END only on the committed text, never a speculative preview."""
    if board.stage != "route" or not board.text or board.text[-1].isspace():
        return False
    if getattr(jev, "paragraph", False) and len(re.findall(r"[.!?](?=\s|$)", board.text)) < 3:
        return False
    options = {"STOP": "The answer is correct and complete as written.",
               "CONTINUE": "The answer still needs a letter, fact, or sentence."}
    answers = jev.ask({"user_question": task, "exact_answer": board.text}, {
        "finish": {"type": "choice", "instructions":
            "Should exact_answer end now? Include the form requested in user_question.",
            "criteria": options},
        "fact": {"type": "noul", "instructions":
            "Does exact_answer fully and factually answer user_question as written?"},
        "form": {"type": "noul", "instructions":
            "Does exact_answer satisfy the form requested in user_question?"},
        "boundary": {"type": "noul", "instructions":
            "Does exact_answer end on a complete, correctly spelled word, number, or punctuation mark?"},
    })
    selected, _ = choice(answers["finish"], options)
    thresholds = {"fact": .60, "form": .70, "boundary": .90}
    return selected == "STOP" and all(probability(answers[key]["noul"]) >= minimum
                                      for key, minimum in thresholds.items())


def generate(jev, task, min_chars=25, max_chars=50, on_character=lambda char: None, initial_board=None):
    if not 1 <= min_chars <= max_chars:
        raise ValueError("Require 1 <= min_chars <= max_chars")
    if getattr(jev, "paragraph", False):
        task += " Use short subject-verb sentences. Put the subject before its finite verb."
    board = initial_board or Keyboard()
    reason = "action_limit"
    for _ in range(max(250, max_chars * 4)):
        if jev is not None and answer_complete(jev, task, board):
            return {"text": board.draft, "completed": True, "reason": "done"}
        if len(board.draft) >= max_chars and board.stage != "route":
            reason = "character_limit"
            break
        action = next_action(jev, task, board, min_chars, max_chars)
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
    parser.add_argument("--paragraph", action="store_true")
    parser.add_argument("--min-chars", type=int)
    parser.add_argument("--max-chars", type=int)
    parser.add_argument("--max-cost", type=float, default=.10)
    args = parser.parse_args()
    args.min_chars = args.min_chars if args.min_chars is not None else (75 if args.paragraph else 25)
    args.max_chars = args.max_chars if args.max_chars is not None else (250 if args.paragraph else 50)
    if not 1 <= args.min_chars <= args.max_chars or not math.isfinite(args.max_cost) or args.max_cost <= 0:
        parser.error("Character limits and max cost must be positive and valid")
    key = os.environ.get("AI_GATEWAY_API_KEY") or getpass.getpass("AI Gateway key: ")
    jev = Jev(key, args.max_cost, args.paragraph)
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
