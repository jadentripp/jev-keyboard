import unittest
import json
from unittest.mock import Mock, patch
import requests

from jev_keyboard import Jev, Keyboard, answer_complete, choice, decide, generate, next_action


class KeyboardTests(unittest.TestCase):
    def test_every_action_is_append_only_and_at_most_one_character(self):
        board = Keyboard()
        for action in ("WORD", "H", "i", "END_WORD", "SPACE", "PUNCTUATION", "!", "DONE"):
            updated = board.apply(action)
            self.assertTrue(updated.draft.startswith(board.draft))
            self.assertIn(len(updated.draft) - len(board.draft), (0, 1))
            board = updated
        self.assertEqual(board.draft, "Hi !")

    def test_words_and_deletion_are_not_actions(self):
        for action in ("hello", "DELETE", "BACKSPACE", ""):
            with self.assertRaises(ValueError):
                Keyboard(stage="word").apply(action)

    def test_contraction_keeps_one_apostrophe_and_a_word_boundary(self):
        board = Keyboard(word="There", stage="word").apply("'").apply("s")
        self.assertEqual(board.apply("END_WORD").draft, "There's")
        with self.assertRaises(ValueError):
            board.apply("'")
        with self.assertRaises(ValueError):
            Keyboard(word="There'", stage="word").apply("END_WORD")
        with self.assertRaises(ValueError):
            Keyboard(text="There's", stage="punctuation").apply("'")
        self.assertEqual(Keyboard(text="'There's", stage="punctuation").apply("'").draft,
                         "'There's'")

    def test_speculative_choices_cannot_finish_the_answer(self):
        class Capture:
            def choose(self, state, prompt, options):
                self.options = options
                return next(iter(options)), {}
        model = Capture()
        for text in ("", "Hi.", "Text long enough", "Text long enough."):
            decide(model, "A task", Keyboard(text), 10, 50)
            self.assertNotIn("DONE", model.options)

    def test_stop_checks_fact_format_and_boundary_on_committed_text(self):
        class Checker:
            def __init__(self, scores, selection="STOP"):
                self.scores, self.selection = scores, selection
                self.calls = 0
            def ask(self, state, questions):
                self.calls += 1
                self.asserted_state = state
                return {"finish": {"type": "choice", "choice": self.selection,
                                   "probabilities": {"STOP": 1 if self.selection == "STOP" else 0,
                                                     "CONTINUE": 1 if self.selection == "CONTINUE" else 0}},
                        **{key: {"noul": value} for key, value in self.scores.items()}}
        for scores, expected in (({"fact": .99, "form": .98, "boundary": .99}, True),
                                 ({"fact": .65, "form": .72, "boundary": .98}, True),
                                 ({"fact": .59, "form": .99, "boundary": .99}, False),
                                 ({"fact": .99, "form": .69, "boundary": .99}, False),
                                 ({"fact": .99, "form": .99, "boundary": .89}, False)):
            checker = Checker(scores)
            self.assertEqual(answer_complete(checker, "Give one word", Keyboard("Paris")), expected)
            self.assertEqual(checker.asserted_state["exact_answer"], "Paris")
        checker = Checker({"fact": .99, "form": .99, "boundary": .99})
        for board in (Keyboard(), Keyboard("Paris "), Keyboard(word="Pa", stage="word")):
            self.assertFalse(answer_complete(checker, "Give one word", board))
        self.assertEqual(checker.calls, 0)

    def test_paragraph_preserves_sentence_spacing_and_excludes_newline(self):
        class Capture:
            paragraph = True
            def choose(self, state, prompt, options):
                self.options = options
                return next(iter(options)), {}
        model = Capture()
        decide(model, "A task", Keyboard("A sentence."), 200, 350)
        self.assertNotIn("WORD", model.options)
        self.assertNotIn("DONE", model.options)
        self.assertIn("SPACE", model.options)
        decide(model, "A task", Keyboard("A sentence", stage="punctuation"), 200, 350)
        self.assertNotIn("ENTER", model.options)
        self.assertTrue(all(mark not in model.options for mark in ".!?"))

    def test_sentence_ending_checks_completeness_and_grammar_without_task(self):
        class Checker:
            paragraph = True
            def __init__(self, grammar, complete=.99):
                self.grammar = grammar
                self.complete = complete
            def ask(self, state, questions):
                assert set(state) == {"sentence"}
                return {"complete": {"noul": self.complete}, "grammar": {"noul": self.grammar}}
            def choose(self, state, prompt, options):
                return "SPACE", {"SPACE": 1}
        board = Keyboard("A complete statement of sufficient length")
        self.assertEqual(decide(Checker(.2), "A task", board, 75, 250)[0], "SPACE")
        self.assertEqual(decide(Checker(.99, .2), "A task", board, 75, 250)[0], "SPACE")
        action = decide(Checker(.95), "A task", board, 75, 250)[0]
        self.assertEqual(action, "END_SENTENCE")
        ending = board.apply(action)
        self.assertEqual(ending.draft, board.draft)
        self.assertEqual(ending.apply(".").draft, board.draft + ".")

    def test_explicit_service_unavailability_has_bounded_retries(self):
        for status in (503, 504, 529):
            with self.subTest(status=status):
                with patch("jev_keyboard.requests.Session.post", return_value=Mock(status_code=status, ok=False)) as call:
                    with patch("jev_keyboard.time.sleep"):
                        with self.assertRaisesRegex(RuntimeError, str(status)):
                            Jev("test-key").ask({}, {})
                self.assertEqual(call.call_count, 7)

    def test_lookahead_discards_the_future_character(self):
        class Ranker:
            def choose(self, state, prompt, options):
                self.previews = list(options.values())
                selected = next(key for key, text in options.items() if json.loads(text).startswith("c"))
                return selected, {key: 1 if key == selected else 0 for key in options}
        model = Ranker()
        board = Keyboard(stage="word")
        def predict(model, task, state, minimum, maximum):
            if not state.word:
                return "a", {"a": .4, "b": .3, "c": .2, "d": .1}
            return "z", {"z": 1}
        with patch("jev_keyboard.decide", side_effect=predict):
            action = next_action(model, "A task", board, 25, 50)
        self.assertTrue(any(json.loads(text).startswith("cz") for text in model.previews))
        self.assertEqual(board.apply(action).draft, "c")
        self.assertEqual(board.draft, "")

    def test_word_boundaries_get_the_same_number_of_preview_characters(self):
        class Ranker:
            def choose(self, state, prompt, options):
                texts = [json.loads(text) for text in options.values()]
                self.lengths = {len(text) for text in texts}
                selected = next(key for key, text in options.items() if json.loads(text).startswith("a "))
                return selected, {key: 1 if key == selected else 0 for key in options}
        def predict(model, task, board, minimum, maximum):
            if board.stage == "route":
                action = "WORD" if board.text.endswith(" ") else "SPACE"
                return action, {action: 1}
            if board.word == "a":
                return "END_WORD", {"END_WORD": .6, "x": .4}
            char = "b" if not board.word else "z"
            return char, {char: 1}
        model = Ranker()
        board = Keyboard(word="a", stage="word")
        with patch("jev_keyboard.decide", side_effect=predict):
            self.assertEqual(next_action(model, "A task", board, 25, 50), "END_WORD")
        self.assertEqual(len(model.lengths), 1)
        self.assertTrue(1 < next(iter(model.lengths)) - len(board.draft) <= 6)

    def test_lookahead_keeps_an_alternative_until_more_context_is_visible(self):
        class Ranker:
            calls = 0
            def choose(self, state, prompt, options):
                self.calls += 1
                prefix = "b" if self.calls == 5 else "a"
                selected = next(key for key, text in options.items() if json.loads(text).startswith(prefix))
                return selected, {key: .9 if key == selected else .1 for key in options}
        def predict(model, task, board, minimum, maximum):
            return ("a", {"a": .6, "b": .4}) if not board.word else ("z", {"z": 1})
        model = Ranker()
        with patch("jev_keyboard.decide", side_effect=predict):
            self.assertEqual(next_action(model, "A task", Keyboard(stage="word"), 25, 50), "b")
        self.assertEqual(model.calls, 5)

    def test_length_limit_is_not_reported_as_completion(self):
        emitted = []
        with patch("jev_keyboard.next_action", side_effect=["WORD", "a", "b"]):
            result = generate(None, "A task", 1, 2, emitted.append)
        self.assertEqual(emitted, ["a", "b"])
        self.assertFalse(result["completed"])
        self.assertEqual(result["reason"], "character_limit")

    def test_model_can_finish_at_the_exact_character_limit(self):
        class Checker:
            def ask(self, state, questions):
                ready = state["exact_answer"] == "Hi."
                return {"finish": {"type": "choice", "choice": "STOP" if ready else "CONTINUE",
                                   "probabilities": {"STOP": 1 if ready else 0,
                                                     "CONTINUE": 0 if ready else 1}},
                        **{key: {"noul": 1 if ready else 0}
                           for key in ("fact", "form", "boundary")}}
        actions = ["WORD", "H", "i", "END_WORD", "PUNCTUATION", "."]
        with patch("jev_keyboard.next_action", side_effect=actions):
            result = generate(Checker(), "A task", 1, 3)
        self.assertEqual(result, {"text": "Hi.", "completed": True, "reason": "done"})

    def test_model_can_end_short_word_below_default_minimum(self):
        class Checker:
            def ask(self, state, questions):
                self.asserted_text = state["exact_answer"]
                return {"finish": {"type": "choice", "choice": "STOP",
                                   "probabilities": {"STOP": 1, "CONTINUE": 0}},
                        **{key: {"noul": .99} for key in ("fact", "form", "boundary")}}
        model = Checker()
        self.assertEqual(generate(model, "Capital? One word.", 25, 50,
                                  initial_board=Keyboard("Paris")),
                         {"text": "Paris", "completed": True, "reason": "done"})
        self.assertEqual(model.asserted_text, "Paris")

    def test_malformed_model_probabilities_are_rejected(self):
        for probabilities in ({"a": float("nan"), "b": 0}, {"a": .1, "b": .9}, {"a": .4, "b": .4}):
            with self.assertRaises(ValueError):
                choice({"type": "choice", "choice": "a", "probabilities": probabilities}, {"a": None, "b": None})

    def test_small_probability_discrepancies_preserve_the_model_selection(self):
        answer = {"type": "choice", "choice": "a", "probabilities": {"a": .495, "b": .505}}
        self.assertEqual(choice(answer, {"a": None, "b": None})[0], "a")

    def test_resume_preserves_the_existing_prefix(self):
        class Checker:
            def ask(self, state, questions):
                ready = state["exact_answer"] == "Existing."
                return {"finish": {"type": "choice", "choice": "STOP" if ready else "CONTINUE",
                                   "probabilities": {"STOP": 1 if ready else 0,
                                                     "CONTINUE": 0 if ready else 1}},
                        **{key: {"noul": 1 if ready else 0}
                           for key in ("fact", "form", "boundary")}}
        emitted = []
        board = Keyboard(text="Existing", stage="punctuation")
        with patch("jev_keyboard.next_action", side_effect=["."]):
            result = generate(Checker(), "A task", 1, 20, emitted.append, board)
        self.assertEqual(result['text'], "Existing.")
        self.assertEqual(emitted, ["."])

    def test_cost_limit_blocks_the_next_request(self):
        def response(*args, **kwargs):
            return Mock(ok=True, json=lambda: {"answers": {}, "provider_metadata": {"gateway": {"cost": ".06"}}})
        model = Jev("test-key", max_cost=.10)
        with patch("jev_keyboard.requests.Session.post", side_effect=response) as call:
            model.ask({}, {})
            model.ask({}, {})
            with self.assertRaises(RuntimeError):
                model.ask({}, {})
            self.assertEqual(call.call_count, 2)

    def test_unknown_network_outcomes_are_not_retried(self):
        with patch("jev_keyboard.requests.Session.post", side_effect=requests.Timeout) as call:
            with self.assertRaisesRegex(RuntimeError, "outcome unknown"):
                Jev("test-key").ask({}, {})
            self.assertEqual(call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
