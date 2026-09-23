"""Focused offline checks for soft lookahead and irreversible boundaries."""
import importlib.util
import unittest
from pathlib import Path

SOURCE = Path(__file__).with_name('beam_prefix_viable.py')
spec = importlib.util.spec_from_file_location('beam_prefix_viable', SOURCE)
beam = importlib.util.module_from_spec(spec)
spec.loader.exec_module(beam)


class MockJev:
    def __init__(self, closed=.9, context=None, role=.9, irreparable=.1,
                 clause=.9, content=.9, full_answer=.9, viability=None,
                 current_complete=.4, word_prefix=None, adjacent=.9,
                 requirement=.8, truthful=.9):
        self.closed = closed
        self.context = closed if context is None else context
        self.role = role
        self.irreparable = irreparable
        self.clause = clause
        self.content = content
        self.full_answer = full_answer
        self.current_complete = current_complete
        self.adjacent = adjacent
        self.requirement = requirement
        self.truthful = truthful
        self.word_prefix = word_prefix or {}
        self.viability = viability or {}

    def ask(self, state, questions):
        result = {}
        for key, item in questions.items():
            if item['type'] == 'choice':
                options = item['criteria']
                probs = dict.fromkeys(options, 0.)
                probs[next(iter(options))] = 1.
                result[key] = {'type': 'choice', 'choice': next(iter(options)),
                               'probabilities': probs}
            else:
                value = self.closed if key in ('word_closed', 'word_isolated') else (
                    self.context if key == 'closed_context' else
                    self.role if key == 'closed_role' else
                    self.adjacent if key == 'adjacent_words' else
                    self.requirement if key == 'prior_requirement' else
                    self.truthful if key == 'truthful_word' else
                    self.irreparable if key == 'closed_irreparable' else
                    self.content if key.startswith('content_') else
                    self.full_answer if key.startswith('complete_answer_') else
                    self.current_complete if key == 'current_word_complete' else
                    self.word_prefix.get(key, .85)
                    if key.startswith('single_word_prefix_') else
                    self.clause if key.startswith('clause_') else
                    self.viability.get(key, .5))
                result[key] = {'type': 'noul', 'noul': value}
        return result


class BeamBoundaryTests(unittest.TestCase):
    def test_unfinished_preview_is_not_hard_rejected(self):
        model = MockJev(viability={'0_prefix_viable': .01,
                                   '0_closed_words': .01})
        rank, _, quality = beam.grade(
            model, 'Explain evaporation in one sentence.',
            [('Th', 1., False), ('E', .1, False)], '')
        self.assertEqual(set(rank), {0, 1})
        self.assertGreater(quality['0']['combined'], 0)

    def test_space_closing_invalid_word_is_rejected(self):
        selected, evidence = beam.choose_commit(
            MockJev(closed=.2), 'What causes rain?', 'Thex',
            [('Thex ', 1., False), ('Thexy', .1, False)], [0, 1])
        self.assertEqual(selected, 'y')
        self.assertEqual(evidence['options'][' ']['reject'], 'closed_word')

    def test_space_after_valid_single_letter_can_commit(self):
        selected, evidence = beam.choose_commit(
            MockJev(closed=.82), 'What is a triangle?', 'A',
            [('A ', 1., False), ('An', .1, False)], [0, 1])
        self.assertEqual(selected, ' ')
        self.assertIsNone(evidence['options'][' ']['reject'])

    def test_context_rejects_closing_wrong_role(self):
        selected, evidence = beam.choose_commit(
            MockJev(closed=.94, context=.2), 'Explain evaporation.',
            'Evaporation as an is',
            [('Evaporation as an is ', 1., False),
             ('Evaporation as an isla', .1, False)], [0, 1])
        self.assertEqual(selected, 'l')
        self.assertEqual(evidence['options'][' ']['reject'],
                         'closed_word_context')

    def test_targeted_role_rejects_repeated_determiner(self):
        selected, evidence = beam.choose_commit(
            MockJev(closed=.9, context=.76, role=.2, irreparable=.82),
            'Explain evaporation.', 'Evaporation is an a',
            [('Evaporation is an a ', 1., False),
             ('Evaporation is an ac', .1, False)], [0, 1])
        self.assertEqual(selected, 'c')
        self.assertEqual(evidence['options'][' ']['reject'],
                         'irreparable_closed_role')

    def test_prior_word_requirement_is_checked_before_space_commits(self):
        selected, evidence = beam.choose_commit(
            MockJev(closed=.95, context=.65, role=.60,
                    irreparable=.35, adjacent=.69, requirement=.10),
            'What is evaporation?', 'Evaporation as an is',
            [('Evaporation as an is ', 1., False),
             ('Evaporation as an isla', .1, False)], [0, 1])
        self.assertEqual(selected, 'l')
        self.assertEqual(evidence['options'][' ']['reject'],
                         'invalid_prior_requirement')

    def test_adjacent_pair_may_await_a_future_word(self):
        selected, evidence = beam.choose_commit(
            MockJev(closed=.95, context=.67, role=.57,
                    irreparable=.35, adjacent=.81),
            'Why do shadows form?', 'Shadows are shown as',
            [('Shadows are shown as ', 1., False),
             ('Shadows are shown ass', .1, False)], [0, 1])
        self.assertEqual(selected, ' ')
        self.assertIsNone(evidence['options'][' ']['reject'])

    def test_low_generic_pair_score_does_not_block_prepositional_phrase(self):
        selected, evidence = beam.choose_commit(
            MockJev(closed=.9, context=.78, role=.5,
                    irreparable=.14, requirement=.18),
            'What is evaporation?', 'Evaporation as',
            [('Evaporation as ', 1., False),
             ('Evaporation asa', .1, False)], [0, 1])
        self.assertEqual(selected, ' ')
        self.assertIsNone(evidence['options'][' ']['reject'])

    def test_false_closed_word_is_rejected(self):
        selected, evidence = beam.choose_commit(
            MockJev(closed=.9, context=.7, requirement=.55,
                    truthful=.28), 'What causes tides?', 'The masses are asses',
            [('The masses are asses ', 1., False),
             ('The masses are assess', .1, False)], [0, 1])
        self.assertEqual(selected, 's')
        self.assertEqual(evidence['options'][' ']['reject'],
                         'untruthful_closed_word')

    def test_sentence_mark_closing_fragment_is_rejected(self):
        selected, evidence = beam.choose_commit(
            MockJev(clause=.3), 'Explain weather.', 'The',
            [('The.', 1., False), ('Ther', .1, False)], [0, 1])
        self.assertEqual(selected, 'r')
        self.assertEqual(evidence['options']['.']['reject'], 'incomplete_clause')

    def test_low_viability_fourth_identical_letter_is_rejected(self):
        selected, evidence = beam.choose_commit(
            MockJev(viability={'viable_97': .26, 'viable_98': .51}),
            'Explain evaporation.', 'erroraaa',
            [('erroraaaa', 1., False), ('erroraaab', .1, False)], [0, 1])
        self.assertEqual(selected, 'b')
        self.assertEqual(evidence['options']['a']['reject'],
                         'irreparable_letter_run')

    def test_overconfident_viability_cannot_append_fourth_identical_letter(self):
        selected, evidence = beam.choose_commit(
            MockJev(viability={'viable_97': .57, 'viable_98': .51}),
            'What causes tides?', 'awwayaaa',
            [('awwayaaaa', 1., False), ('awwayaaab', .1, False)], [0, 1])
        self.assertEqual(selected, 'b')
        self.assertEqual(evidence['options']['a']['reject'],
                         'irreparable_letter_run')

    def test_tautological_sentence_ending_cannot_commit(self):
        selected, evidence = beam.choose_commit(
            MockJev(clause=.74, content=.55, full_answer=.04),
            'What is evaporation? Answer in one sentence.',
            'Evaporation is an evaporation',
            [('Evaporation is an evaporation.', 1., False),
             ('Evaporation is an evaporations', .1, False)], [0, 1])
        self.assertEqual(selected, 's')
        self.assertEqual(evidence['options']['.']['reject'],
                         'incomplete_single_sentence')

    def test_complete_word_cannot_append_nonword_suffix(self):
        selected, evidence = beam.choose_commit(
            MockJev(current_complete=.95,
                    word_prefix={'single_word_prefix_105': .20}),
            'What is evaporation?', 'Evaporation',
            [('Evaporationi', 1., False), ('Evaporation ', .1, False)], [0, 1])
        self.assertEqual(selected, ' ')
        self.assertEqual(evidence['options']['i']['reject'],
                         'joined_or_misspelled_word')

    def test_no_credible_append_only_continuation_terminates(self):
        with self.assertRaisesRegex(RuntimeError, 'no viable append-only'):
            beam.choose_commit(
                MockJev(viability={'viable_97': .22, 'viable_98': .28}),
                'Explain evaporation.', 'errorxyz',
                [('errorxyza', 1., False), ('errorxyzb', .1, False)], [0, 1])


if __name__ == '__main__':
    unittest.main()
