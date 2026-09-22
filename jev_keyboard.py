#!/usr/bin/env python3
"""Experimental character generation using Jev Choice predictions. Python 3.10+."""
from __future__ import annotations

import argparse
import getpass
import json
import math
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

ENDPOINT = "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
MODEL = "typesafe-ai/jev"
ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
CHARS = "".join(ROWS) + "".join(ROWS).upper() + "0123456789" + "`~!@#$%^&*()-_=+[{]}\\|;:'\",<.>/?"
KEYS = {c: None for c in CHARS}
KEYS.update({
    "SPACE": "Append one space character.",
    "ENTER": "Append one newline character.",
    "DONE": "Finish: the response is complete and needs no more text.",
})


class GatewayError(RuntimeError):
    pass


def request_body(task: str, draft: str) -> dict:
    return {
        "model": MODEL,
        "state": {"task": task, "draft": draft},
        "questions": {
            "next_key": {
                "type": "choice",
                "instructions": "Next character? Choose DONE when finished.",
                "criteria": KEYS,
            }
        },
    }


def apply_key(draft: str, key: str) -> tuple[str, bool]:
    if key not in KEYS:
        raise ValueError("Jev returned an unknown keyboard action")
    if key == "DONE":
        return draft, True
    return draft + {"SPACE": " ", "ENTER": "\n"}.get(key, key), False


def parse_answer(response: dict, criteria: dict | None = None) -> dict:
    criteria = KEYS if criteria is None else criteria
    answer = response["answers"]["next_key"]
    probabilities = answer["probabilities"]
    if answer.get("type") != "choice" or answer.get("choice") not in criteria:
        raise ValueError("Invalid Choice answer")
    if set(probabilities) != set(criteria):
        raise ValueError("Incomplete keyboard probability distribution")
    if any(not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1
           for p in probabilities.values()):
        raise ValueError("Invalid probability")
    if not math.isclose(sum(probabilities.values()), 1, abs_tol=0.02):
        raise ValueError("Keyboard probabilities do not sum to one")
    if probabilities[answer["choice"]] + 1e-6 < max(probabilities.values()):
        raise ValueError("Selected key disagrees with probability distribution")
    return answer


class WordKeyboard:
    """Route each text unit, then spell its word or select its punctuation."""

    def __init__(self):
        self.stage = "route"
        self.text = ""
        self.word = ""
        self.completed_words = []

    @property
    def draft(self):
        return self.text + self.word

    def body(self, task):
        if self.stage == "route":
            instructions = "What comes next in the response?"
            criteria = {k: None for k in ("WORD", "SPACE", "PUNCTUATION", "DONE")}
            if not self.text:
                criteria.pop("SPACE")
                criteria.pop("DONE")
            else:
                if self.text[-1].isspace():
                    criteria.pop("SPACE")
                if self.text[-1].isalnum():
                    criteria.pop("WORD")
        elif self.stage == "word":
            instructions = "Which is the correct next prefix of the answer?"
            criteria = {c: self.draft + c for c in CHARS if c.isalnum()}
            if self.word:
                criteria["END_WORD"] = self.draft + " (complete word)"
        else:
            instructions = "Which punctuation mark?"
            criteria = {c: None for c in CHARS if not c.isalnum()}
            criteria["ENTER"] = "Newline"
        state = {"task": task, "text": self.text}
        if self.stage == "word":
            state["current_word"] = self.word
        return {
            "model": MODEL,
            "state": state,
            "questions": {"next_key": {"type": "choice", "instructions": instructions,
                                       "criteria": criteria}},
        }

    def apply(self, action):
        if action not in self.body("")["questions"]["next_key"]["criteria"]:
            raise ValueError("Invalid action for current stage")
        if self.stage == "route":
            if action == "DONE":
                return True
            if action == "SPACE":
                self.text += " "
            else:
                self.stage = "word" if action == "WORD" else "punctuation"
        elif self.stage == "word":
            if action == "END_WORD":
                self.completed_words.append(self.word)
                self.text += self.word
                self.word = ""
                self.stage = "route"
            else:
                self.word += action
        else:
            self.text += "\n" if action == "ENTER" else action
            self.stage = "route"
        return False


def evaluate(body: dict, api_key: str, timeout: float = 45) -> dict:
    req = urllib.request.Request(ENDPOINT, json.dumps(body).encode(), headers={
        "Authorization": "Bearer " + api_key, "Content-Type": "application/json",
    })
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            # Never print request headers or potentially echoed request bodies.
            if error.code == 402:
                raise GatewayError("AI Gateway requires a positive credit balance (HTTP 402).") from None
            if error.code in (401, 403):
                raise GatewayError(f"AI Gateway rejected credentials/access (HTTP {error.code}).") from None
            if error.code in (429, 500, 502, 503, 504, 529) and attempt < 3:
                retry = error.headers.get("Retry-After", "")
                delay = float(retry) if retry.isdigit() else 2 ** attempt
                if delay > 60:
                    raise GatewayError("Gateway requested a retry after more than 60 seconds; stopped.") from None
                time.sleep(delay)
                continue
            raise GatewayError(f"AI Gateway request failed (HTTP {error.code}).") from None
        except (urllib.error.URLError, TimeoutError):
            # Do not automatically retry unknown outcomes that may have been billed.
            raise GatewayError("Network failure; request outcome unknown. Stopped without retrying.") from None
    raise GatewayError("Retry limit reached")


def generate(task: str, api_key: str, trace_path: Path, *, max_steps: int = 1500,
             max_seconds: float = 1800, max_cost: float = 0.25,
             call=evaluate, on_change=None, mode="word", stop_file: Path | None = None,
             resume_trace: Path | None = None) -> dict:
    if max_steps < 1 or max_seconds <= 0 or max_cost <= 0:
        raise ValueError("Step, time and cost limits must be positive")
    draft = ""
    if mode not in ("word", "character", "scored", "planned", "direct", "boundary"):
        raise ValueError("Unknown mode")
    if mode == "boundary":
        from direct_keyboard import BoundaryKeyboard
        keyboard = BoundaryKeyboard()
    elif mode == "direct":
        from direct_keyboard import DirectKeyboard
        keyboard = DirectKeyboard()
    elif mode == "planned":
        from planned_keyboard import PlannedKeyboard
        keyboard = PlannedKeyboard()
    else:
        keyboard = WordKeyboard() if mode in ("word", "scored") else None
    if resume_trace is not None:
        seen_sources = set()
        def replay(path):
            nonlocal draft
            path = Path(path).resolve()
            if path in seen_sources:
                raise ValueError("Cycle in resume trace chain")
            seen_sources.add(path)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            start = rows[0]
            if start.get("event") != "start" or start.get("task") != task or start.get("mode") != mode:
                raise ValueError("Resume trace task or mode differs")
            if start.get("resume_source"):
                replay(path.parent / start["resume_source"])
            if draft != start.get("initial_draft", ""):
                raise ValueError("Resume trace initial draft differs")
            for row in rows:
                if row.get("event") != "key":
                    continue
                if row["before"] != draft:
                    raise ValueError("Resume trace is discontinuous")
                action = row.get("selection", row["response"]["answers"].get("next_key", {}))["choice"]
                if keyboard:
                    keyboard.body(task)
                    keyboard.apply(action)
                    draft = keyboard.draft
                else:
                    draft, _ = apply_key(draft, action)
                if draft != row["after"]:
                    raise ValueError("Resume action differs from recorded text")
        replay(resume_trace)
    started = time.monotonic()
    usage = {"input_tokens": 0, "output_tokens": 0}
    known_cost = 0.0
    cost_complete = True
    visits = {draft: 1}
    reason = "max_steps"
    count = 0
    error_message = None
    request_outcome_unknown = False
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental destruction of previous evidence.
    with trace_path.open("x", encoding="utf-8") as log:
        def record(event):
            log.write(json.dumps(event, ensure_ascii=False) + "\n")
            log.flush()
        record({"event": "start", "model": MODEL, "task": task,
                "keyboard": KEYS if keyboard is None else "staged_word_keyboard",
                "mode": mode, "initial_draft": draft,
                "resume_source": os.path.relpath(resume_trace, trace_path.parent) if resume_trace else None,
                "max_steps": max_steps,
                "max_seconds": max_seconds, "max_cost_usd": max_cost})
        for step in range(max_steps):
            if stop_file is not None and stop_file.exists():
                reason = "cancelled"
                break
            if time.monotonic() - started >= max_seconds:
                reason = "max_seconds"
                break
            before = draft
            tick = time.monotonic()
            body = keyboard.body(task) if keyboard else request_body(task, draft)
            score_actions = None
            if mode == "scored" and keyboard.stage == "word":
                score_actions = list(body["questions"]["next_key"]["criteria"])
                body = {"model": MODEL, "state": {"task": task}, "questions": {
                    str(i): {"type": "noul", "instructions": "Could " + json.dumps(
                        draft + (" " if action == "END_WORD" else action)) +
                        " naturally begin a correct answer?"}
                    for i, action in enumerate(score_actions)}}
                if keyboard.word:
                    body["questions"]["word_complete"] = {
                        "type": "noul", "instructions": "Is " + json.dumps(keyboard.word) +
                        " a fully spelled word in a correct answer to the task?"}
            try:
                response = call(body, api_key)
                if score_actions is None:
                    answer = parse_answer(response, body["questions"]["next_key"]["criteria"])
                    if hasattr(keyboard, "select_answer"):
                        answer = keyboard.select_answer(answer, response)
                else:
                    scores = {action: response["answers"][str(i)]["noul"]
                              for i, action in enumerate(score_actions)}
                    if any(not isinstance(s, (int, float)) or not math.isfinite(s) or not 0 <= s <= 1
                           for s in scores.values()):
                        raise ValueError("Invalid character plausibility score")
                    eligible = dict(scores)
                    completeness = None
                    if "END_WORD" in eligible:
                        completeness = response["answers"]["word_complete"]["noul"]
                        if not isinstance(completeness, (int, float)) or not math.isfinite(completeness) or not 0 <= completeness <= 1:
                            raise ValueError("Invalid word completeness score")
                        if completeness < 0.8:
                            eligible.pop("END_WORD")
                    answer = {"choice": max(eligible, key=eligible.get),
                              "selection_method": "highest_noul_with_word_boundary_gate",
                              "scores": scores, "word_completeness": completeness}
            except (GatewayError, ValueError, KeyError, TypeError) as error:
                reason = "error"
                error_message = str(error)
                request_outcome_unknown = isinstance(error, GatewayError) and "outcome unknown" in str(error)
                record({"event": "error", "message": error_message})
                break
            except KeyboardInterrupt:
                reason = "interrupted"
                request_outcome_unknown = True
                break
            count += 1
            for name in usage:
                usage[name] += int(response.get("usage", {}).get(name, 0))
            gateway = response.get("provider_metadata", {}).get("gateway", {})
            cost = gateway.get("cost")
            if cost is None:
                cost_complete = False
            else:
                try:
                    numeric_cost = float(cost)
                    if not math.isfinite(numeric_cost) or numeric_cost < 0:
                        raise ValueError()
                    known_cost += numeric_cost
                except (TypeError, ValueError):
                    cost_complete = False
            if keyboard:
                done = keyboard.apply(answer["choice"])
                draft = keyboard.draft
            else:
                draft, done = apply_key(draft, answer["choice"])
            # A model action may append one character or only change stages.
            # Whole-word selections and edits are never valid generation steps.
            if not draft.startswith(before) or len(draft) - len(before) not in (0, 1):
                raise AssertionError("Generation must append at most one character per action")
            record({"event": "key", "step": step, "before": before, "after": draft,
                    "latency_seconds": time.monotonic() - tick, "request": body,
                    "response": response, "selection": answer})
            if on_change:
                on_change(draft, answer["choice"])
            if done:
                reason = "done"
                break
            state_key = (draft, keyboard.stage) if keyboard else draft
            visits[state_key] = visits.get(state_key, 0) + 1
            if visits[state_key] >= 4 or (len(draft) >= 12 and len(set(draft[-12:])) == 1):
                reason = "repetition"
                break
            if keyboard and len(keyboard.completed_words) >= 4:
                last_words = [w.casefold() for w in keyboard.completed_words[-4:]]
                if len(set(last_words)) == 1:
                    reason = "repeated_words"
                    break
            if not cost_complete:
                reason = "cost_metadata_missing"
                break
            if known_cost >= max_cost:
                reason = "cost_limit"
                break
        summary = {"event": "summary", "text": draft, "stop_reason": reason,
                   "mode": mode, "completed_words": keyboard.completed_words if keyboard else None,
                   "completed": reason == "done", "steps": count,
                   "elapsed_seconds": time.monotonic() - started, "usage": usage,
                   "reported_cost_usd": known_cost, "cost_metadata_complete": cost_complete,
                   "request_outcome_unknown": request_outcome_unknown,
                   "error": error_message}
        record(summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", help="Task or question for Jev")
    parser.add_argument("--trace", type=Path,
                        default=Path("jev-run-" + str(time.time_ns()) + ".jsonl"))
    parser.add_argument("--max-steps", type=int, default=1500)
    parser.add_argument("--max-seconds", type=float, default=1800)
    parser.add_argument("--max-cost", type=float, default=0.25)
    parser.add_argument("--mode", choices=("word", "character", "scored", "planned", "direct", "boundary"), default="word")
    parser.add_argument("--show-request", action="store_true", help="Print initial request; no API call")
    parser.add_argument("--stop-file", type=Path, help="Stop between requests when this file exists")
    parser.add_argument("--resume-trace", type=Path, help="Continue the exact recorded character sequence")
    args = parser.parse_args()
    if args.show_request:
        if args.mode == "boundary":
            from direct_keyboard import BoundaryKeyboard
            body = BoundaryKeyboard().body(args.task)
        elif args.mode == "direct":
            from direct_keyboard import DirectKeyboard
            body = DirectKeyboard().body(args.task)
        elif args.mode == "planned":
            from planned_keyboard import PlannedKeyboard
            body = PlannedKeyboard().body(args.task)
        else:
            body = WordKeyboard().body(args.task) if args.mode in ("word", "scored") else request_body(args.task, "")
        print(json.dumps(body, indent=2))
        return 0
    key = os.environ.get("AI_GATEWAY_API_KEY") or getpass.getpass("AI Gateway key (not saved): ")
    if not key.strip():
        parser.error("An API key is required")
    displayed = ""
    def display(draft, action):
        nonlocal displayed
        sys.stdout.write(draft[len(displayed):])
        displayed = draft
        sys.stdout.flush()
    result = generate(args.task, key, args.trace, max_steps=args.max_steps,
                      max_seconds=args.max_seconds, max_cost=args.max_cost,
                      on_change=display, mode=args.mode, stop_file=args.stop_file,
                      resume_trace=args.resume_trace)
    print("\n" + json.dumps(result, indent=2), file=sys.stderr)
    return 0 if result["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
