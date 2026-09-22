import io
import json
import unittest
from unittest.mock import patch

from jev_keyboard import Jev, Keyboard, choice, decide, generate, next_action


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

    def test_done_requires_length_and_punctuation(self):
        class Capture:
            def choose(self, state, prompt, options):
                self.options = options
                return next(iter(options)), {}
        model = Capture()
        for text, allowed in (("", False), ("Hi.", False), ("Text long enough", False), ("Text long enough.", True)):
            decide(model, "A task", Keyboard(text), 10, 50)
            self.assertEqual("DONE" in model.options, allowed)

    def test_lookahead_discards_the_future_character(self):
        class Ranker:
            def choose(self, state, prompt, options):
                self.previews = list(options.values())
                return "2", {}
        model = Ranker()
        board = Keyboard(stage="word")
        def predict(model, task, state, minimum, maximum):
            if not state.word:
                return "a", {"a": .4, "b": .3, "c": .2, "d": .1}
            return "z", {"z": 1}
        with patch("jev_keyboard.decide", side_effect=predict):
            action = next_action(model, "A task", board, 25, 50)
        self.assertEqual(model.previews, ["az", "bz", "cz", "dz"])
        self.assertEqual(board.apply(action).draft, "c")
        self.assertEqual(board.draft, "")

    def test_length_limit_is_not_reported_as_completion(self):
        emitted = []
        with patch("jev_keyboard.next_action", side_effect=["WORD", "a", "b"]):
            result = generate(None, "A task", 1, 2, emitted.append)
        self.assertEqual(emitted, ["a", "b"])
        self.assertFalse(result["completed"])
        self.assertEqual(result["reason"], "character_limit")

    def test_model_can_finish_at_the_exact_character_limit(self):
        actions = ["WORD", "H", "i", "END_WORD", "PUNCTUATION", ".", "DONE"]
        with patch("jev_keyboard.next_action", side_effect=actions):
            result = generate(None, "A task", 1, 3)
        self.assertEqual(result, {"text": "Hi.", "completed": True, "reason": "done"})

    def test_malformed_model_probabilities_are_rejected(self):
        for probabilities in ({"a": float("nan"), "b": 0}, {"a": .1, "b": .9}, {"a": .4, "b": .4}):
            with self.assertRaises(ValueError):
                choice({"type": "choice", "choice": "a", "probabilities": probabilities}, {"a": None, "b": None})

    def test_cost_limit_blocks_the_next_request(self):
        def response(*args, **kwargs):
            return io.BytesIO(json.dumps({"answers": {}, "provider_metadata": {"gateway": {"cost": ".06"}}}).encode())
        model = Jev("test-key", max_cost=.10)
        with patch("jev_keyboard.urllib.request.urlopen", side_effect=response) as call:
            model.ask({}, {})
            model.ask({}, {})
            with self.assertRaises(RuntimeError):
                model.ask({}, {})
            self.assertEqual(call.call_count, 2)

    def test_unknown_network_outcomes_are_not_retried(self):
        with patch("jev_keyboard.urllib.request.urlopen", side_effect=TimeoutError) as call:
            with self.assertRaisesRegex(RuntimeError, "outcome unknown"):
                Jev("test-key").ask({}, {})
            self.assertEqual(call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
