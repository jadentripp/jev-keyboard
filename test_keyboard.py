import json
from pathlib import Path
import tempfile
import unittest
from jev_keyboard import KEYS, GatewayError, WordKeyboard, apply_key, generate, parse_answer


def response(key):
    return {"answers": {"next_key": {"type": "choice", "choice": key,
            "confidence": 1, "probabilities": {k: float(k == key) for k in KEYS}}},
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "provider_metadata": {"gateway": {"cost": "0.0000042"}}}


class KeyboardTests(unittest.TestCase):
    def run_case(self, actions, **kwargs):
        actions = iter(actions)
        seen = []
        def call(body, key):
            seen.append(body["state"]["draft"])
            return response(next(actions))
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            result = generate("Ask a question", "test-secret", trace, call=call, mode="character", **kwargs)
            raw = trace.read_text()
            self.assertNotIn("test-secret", raw)
            self.assertEqual(json.loads(raw.splitlines()[-1])["text"], result["text"])
        return result, seen

    def test_keyboard_is_append_only(self):
        self.assertLessEqual(len(KEYS), 255)
        text = ""
        for key in ["H", "i", "SPACE", "A", "?", "ENTER"]:
            text, _ = apply_key(text, key)
        self.assertEqual(text, "Hi A?\n")
        self.assertNotIn("BACKSPACE", KEYS)
        self.assertNotIn("DELETE", KEYS)
        with self.assertRaises(ValueError):
            apply_key("Hi", "BACKSPACE")

    def test_feedback_and_done(self):
        result, seen = self.run_case(["W", "h", "y", "?", "DONE"])
        self.assertEqual(seen, ["", "W", "Wh", "Why", "Why?"])
        self.assertTrue(result["completed"])
        self.assertEqual(result["usage"]["input_tokens"], 500)

    def test_repetition_stops(self):
        result, _ = self.run_case(["SPACE"] * 12)
        self.assertEqual(result["stop_reason"], "repetition")

    def test_budget_stops(self):
        result, _ = self.run_case(["W", "h"], max_cost=0.000005)
        self.assertEqual(result["stop_reason"], "cost_limit")

    def test_reject_unknown_key(self):
        bad = response("INVALID")
        with self.assertRaises(ValueError):
            parse_answer(bad)

    def test_credit_error_is_not_success(self):
        def blocked(body, key):
            raise GatewayError("HTTP 402")
        with tempfile.TemporaryDirectory() as directory:
            result = generate("Ask a question", "secret", Path(directory) / "run.jsonl", call=blocked)
        self.assertFalse(result["completed"])
        self.assertEqual(result["text"], "")
        self.assertEqual(result["stop_reason"], "error")

    def test_word_stage_transition_and_punctuation(self):
        keyboard = WordKeyboard()
        for action in ["WORD", "H", "o", "w", "END_WORD", "SPACE", "WORD", "s", "o", "END_WORD", "PUNCTUATION", "?", "DONE"]:
            body = keyboard.body("Write a question")
            options = body["questions"]["next_key"]["criteria"]
            self.assertNotIn("BACKSPACE", options)
            self.assertNotIn("DELETE", options)
            if keyboard.stage == "word":
                self.assertNotIn("SPACE", options)
                self.assertNotIn("?", options)
            before = keyboard.draft
            done = keyboard.apply(action)
            self.assertTrue(keyboard.draft.startswith(before))
            self.assertIn(len(keyboard.draft) - len(before), (0, 1))
            self.assertEqual(done, action == "DONE")
        self.assertEqual(keyboard.draft, "How so?")
        self.assertEqual(keyboard.completed_words, ["How", "so"])

    def test_staged_loop_feedback(self):
        actions = iter(["WORD", "W", "h", "y", "END_WORD", "PUNCTUATION", "?", "DONE"])
        seen = []
        def call(body, key):
            seen.append(body["state"].copy())
            options = body["questions"]["next_key"]["criteria"]
            action = next(actions)
            self.assertIn(action, options)
            result = response(action)
            result["answers"]["next_key"]["probabilities"] = {k: float(k == action) for k in options}
            return result
        with tempfile.TemporaryDirectory() as directory:
            result = generate("Ask a question", "test-secret", Path(directory) / "trace.jsonl", call=call)
        self.assertEqual(result["text"], "Why?")
        self.assertTrue(result["completed"])
        self.assertEqual(seen[3]["current_word"], "Wh")
        self.assertEqual(seen[5]["text"], "Why")

    def test_word_candidates_show_resulting_text(self):
        keyboard = WordKeyboard()
        for action in ["WORD", "H", "i", "END_WORD", "SPACE", "WORD", "t"]:
            keyboard.apply(action)
        options = keyboard.body("Greet someone")["questions"]["next_key"]["criteria"]
        self.assertEqual(options["h"], "Hi th")
        self.assertEqual(options["END_WORD"], "Hi t (complete word)")
        self.assertNotIn("SPACE", options)

    def test_word_boundaries_do_not_insert_or_delete_text(self):
        keyboard = WordKeyboard()
        options = lambda: keyboard.body("Reply")["questions"]["next_key"]["criteria"]
        self.assertNotIn("SPACE", options())
        self.assertNotIn("DONE", options())
        for action in ["WORD", "A", "END_WORD"]:
            keyboard.apply(action)
        self.assertEqual(keyboard.draft, "A")
        self.assertNotIn("WORD", options())
        self.assertIn("SPACE", options())
        self.assertIn("DONE", options())
        keyboard.apply("SPACE")
        self.assertEqual(keyboard.draft, "A ")
        self.assertNotIn("SPACE", options())
        self.assertIn("WORD", options())

    def test_whole_word_modes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for mode in ('lexicon', 'tournament', 'hints'):
                with self.assertRaises(ValueError):
                    generate('Reply', 'secret', Path(directory) / (mode+'.jsonl'), mode=mode)

    def test_cancel_before_network_request(self):
        with tempfile.TemporaryDirectory() as directory:
            stop = Path(directory) / 'stop'
            stop.touch()
            def forbidden_call(*args):
                self.fail('A cancelled run must not make an API request')
            result = generate('Reply', 'secret', Path(directory) / 'run.jsonl',
                              stop_file=stop, call=forbidden_call)
        self.assertEqual(result['stop_reason'], 'cancelled')
        self.assertEqual(result['steps'], 0)

    def test_interrupted_request_has_unknown_outcome(self):
        def interrupt(*args):
            raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as directory:
            result = generate('Reply', 'secret', Path(directory) / 'run.jsonl', call=interrupt)
        self.assertEqual(result['stop_reason'], 'interrupted')
        self.assertTrue(result['request_outcome_unknown'])

    def test_scored_mode_gates_premature_word_end(self):
        calls = 0
        def call(body, key):
            nonlocal calls
            calls += 1
            if 'next_key' in body['questions']:
                options = body['questions']['next_key']['criteria']
                result = response('WORD')
                result['answers']['next_key']['probabilities'] = {k:float(k=='WORD') for k in options}
                return result
            answers = {}
            for name, question in body['questions'].items():
                if name == 'word_complete':
                    score = 0.1
                else:
                    candidate = json.loads(question['instructions'].removeprefix('Could ').removesuffix(' naturally begin a correct answer?'))
                    score = .99 if candidate.endswith(' ') else (.8 if candidate in ('H','Hi') else .01)
                answers[name] = {'type':'noul','noul':score}
            return {'answers':answers,'usage':{},'provider_metadata':{'gateway':{'cost':0}}}
        with tempfile.TemporaryDirectory() as directory:
            result = generate('Greet someone', 'secret', Path(directory)/'trace.jsonl',
                              mode='scored', max_steps=3, call=call)
        self.assertEqual(result['text'], 'Hi')
        self.assertEqual(result['steps'], 3)

    def test_resume_preserves_model_generated_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory)/'first.jsonl'
            generate('Greet someone','secret',first,mode='character',max_steps=1,
                     call=lambda *args: response('H'))
            actions = iter(['i','DONE'])
            seen = []
            def call(body,key):
                seen.append(body['state']['draft'])
                return response(next(actions))
            second = Path(directory)/'second.jsonl'
            result = generate('Greet someone','secret',second,mode='character',
                              resume_trace=first,call=call)
            self.assertEqual(seen,['H','Hi'])
            self.assertEqual(result['text'],'Hi')
            self.assertTrue(result['completed'])
            with self.assertRaises(ValueError):
                generate('Different task','secret',Path(directory)/'bad.jsonl',
                         mode='character',resume_trace=first,call=call)

    def test_boundary_judgment_never_supplies_or_appends_a_word(self):
        from direct_keyboard import BoundaryKeyboard
        keyboard = BoundaryKeyboard()
        task = 'Greet someone'
        keyboard.apply('WORD')
        body = keyboard.body(task)
        self.assertEqual(body['questions']['next_key']['instructions'],task)
        self.assertNotIn('possible_spellings',json.dumps(body))
        first = next(k for k,v in keyboard.actions.items() if v=='H')
        keyboard.apply(first)
        body = keyboard.body(task)
        second = next(k for k,v in keyboard.actions.items() if v=='i')
        result = keyboard.select_answer({'choice':second},{'answers':{'word_complete':{'noul':.1}}})
        keyboard.apply(result['choice'])
        self.assertEqual(keyboard.draft,'Hi')
        body = keyboard.body(task)
        result = keyboard.select_answer({'choice':second},{'answers':{'word_complete':{'noul':.99}}})
        keyboard.apply(result['choice'])
        self.assertEqual(keyboard.draft,'Hi')
        self.assertEqual(keyboard.stage,'route')


if __name__ == "__main__":
    unittest.main()
