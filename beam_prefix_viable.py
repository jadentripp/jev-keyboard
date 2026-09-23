"""Jev character beam with soft lookahead and guarded irreversible commits."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import string
import sys
import time

import requests

LOCAL = Path(__file__).resolve().parent
DEPENDENCY = LOCAL / 'jev_keyboard.py'
if not DEPENDENCY.exists():
    DEPENDENCY = LOCAL.parent / 'jev-keyboard-github' / 'jev_keyboard.py'
sys.path.insert(0, str(DEPENDENCY.parent))
from jev_keyboard import ENDPOINT, Jev, choice, probability

LETTERS = string.ascii_lowercase
PUNCT = '.?!,;:'


def candidate_chars(prefix):
    if not prefix:
        return string.ascii_uppercase + string.digits
    if prefix[-1] in '.?!':
        return ' '
    if prefix[-1].isspace():
        if prefix.rstrip().endswith(('.', '?', '!')):
            return string.ascii_uppercase + string.digits
        return LETTERS + string.digits
    if prefix[-1] == "'":
        return LETTERS
    current_word = prefix.rsplit(maxsplit=1)[-1]
    apostrophe = "" if "'" in current_word else "'"
    return LETTERS + string.digits + apostrophe + ' ' + PUNCT


def keys(prefix):
    return {str(i): json.dumps(prefix + char) for i, char in enumerate(candidate_chars(prefix))}


def expansions(model, question, paths, per_node=8):
    qs = {}
    options = {}
    for index, (prefix, _, terminated) in enumerate(paths):
        if terminated:
            continue
        opts = keys(prefix)
        options[index] = opts
        qs['n' + str(index)] = {
            'type': 'choice',
            'instructions': ('Select the single next character for a direct, factually accurate answer to the user question. '
                             'Each choice shows the complete answer prefix after that character is appended. '
                             'The last word may be unfinished. Favor paths that can finish as correctly spelled English. '
                             'For sentences, put a finite verb after the subject phrase. Do not end a sentence before its verb.'),
            'criteria': opts,
        }
    answers = model.ask({'user_question': question}, qs) if qs else {}
    result = []
    details = []
    for index, (prefix, weight, terminated) in enumerate(paths):
        if terminated:
            result.append((prefix, weight, True))
            details.append({'prefix': prefix, 'terminal': True})
            continue
        selected, probs = choice(answers['n' + str(index)], options[index])
        order = sorted(probs, key=probs.get, reverse=True)
        # A tiny next-letter probability can still be the only spelling of
        # the right answer. Keep the actual runner-up; let deeper context
        # judge it once it has enough letters to become recognizable.
        picks = order[:per_node]
        detail = {'prefix': prefix, 'best': [(json.loads(options[index][k]), round(probs[k], 3)) for k in picks], 'selected': json.loads(options[index][selected])}
        details.append(detail)
        for key in picks:
            candidate = json.loads(options[index][key])
            if not candidate.startswith(prefix) or len(candidate) != len(prefix) + 1:
                raise ValueError('Non-character candidate')
            result.append((candidate, weight * max(probs[key], .001), False))
    return result, details


def grade(model, question, candidates, committed):
    """Lookahead only ranks possibilities; it never rules out a spelling."""
    options = {str(i): json.dumps(prefix)
               for i, (prefix, _, _) in enumerate(candidates)}
    questions = {'rank': {'type': 'choice', 'instructions':
        'Which possible continuation is a promising beginning of a direct, '
        'factually correct answer to user_question? Prefixes may end midword '
        'or before a verb. Compare what they can become if characters are '
        'appended; do not require a speculative prefix to be finished.',
        'criteria': options}}
    for key, (prefix, _, _) in enumerate(candidates):
        view = json.dumps(prefix)
        questions[str(key) + '_prefix_viable'] = {'type': 'noul',
            'instructions':
            'Could exact speculative prefix ' + view + ' be completed by '
            'APPENDING characters only into a correct answer to '
            'user_question in the requested form? The last word may be '
            'unfinished and a subject may still need a verb.'}
        questions[str(key) + '_closed_words'] = {'type': 'noul',
            'instructions':
            'Are the words that have ALREADY ended with spaces in speculative '
            'prefix ' + view + ' spelled acceptably? Ignore its last '
            'unfinished word and do not require a complete sentence yet.'}
    answers = model.ask({'user_question': question,
                         'committed_answer': committed}, questions)
    _, distribution = choice(answers['rank'], options)
    quality = {}
    for key, (prefix, _, _) in enumerate(candidates):
        viability = probability(answers[str(key) + '_prefix_viable']['noul'])
        spelling = probability(answers[str(key) + '_closed_words']['noul'])
        # No speculative score is a hard veto. In particular, low certainty
        # about unfinished words or a missing future verb cannot erase a path.
        quality[str(key)] = {
            'prefix_viability': viability, 'closed_words': spelling,
            'combined': max(distribution[str(key)], .001) *
                        (.30 + .70 * viability) * (.55 + .45 * spelling)}
    order = sorted(quality, key=lambda key: quality[key]['combined'], reverse=True)
    return [int(key) for key in order], distribution, quality


def choose_commit(model, question, text, candidates, rank):
    """Judge only the next visible character at the exact committed prefix.

    A space closes a word; punctuation closes a clause or sentence. These
    irreversible boundaries need a strong check. Letter viability is useful
    evidence but never a categorical spelling veto on an unfinished word.
    """
    by_char = {}
    for index in rank:
        candidate = candidates[index][0]
        if candidate.startswith(text) and len(candidate) > len(text):
            char = candidate[len(text)]
            by_char.setdefault(char, index)
    if not by_char:
        raise RuntimeError('No candidate has a new character')
    current_word = re.search(r"[A-Za-z]+(?:'[A-Za-z]+)*$", text)
    previous_words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)*", text[:current_word.start()]) if current_word else []
    questions = {}
    if current_word and any(char.isalpha() for char in by_char):
        questions['current_word_complete'] = {'type': 'noul', 'instructions':
            'Is exact_current_word ' + json.dumps(current_word.group()) +
            ' already a complete, correctly spelled single English word?'}
    for char in by_char:
        new = text + char
        token = json.dumps(new)
        questions['viable_' + str(ord(char))] = {'type': 'noul',
            'instructions':
            'Can exact_prefix ' + token + ' be completed into a factually '
            'correct answer to user_question by APPENDING characters only? '
            'An unfinished last word, or a subject waiting for a verb, is '
            'allowed. Judge this exact prefix, not a rewritten answer.'}
        if current_word and char.isalpha():
            questions['single_word_prefix_' + str(ord(char))] = {
                'type': 'noul', 'instructions':
                'Is candidate_word ' + json.dumps(current_word.group() + char) +
                ' a plausible spelling prefix of ONE standard English word? '
                'It can be unfinished, but two words accidentally joined '
                'without a space do not count.'}
        if char in '.?!;:':
            questions['clause_' + str(ord(char))] = {'type': 'noul',
                'instructions':
                'At exact_prefix ' + token + ', does this punctuation close '
                'a grammatical, relevant complete sentence (for . ? !) '
                'or complete clause (for ; :) about user_question?'}
        if char in '.?!':
            questions['content_' + str(ord(char))] = {'type': 'noul',
                'instructions':
                'If exact_prefix ' + token + ' ends this sentence now, does '
                'the finished sentence give concrete and useful information '
                'relevant to user_question, beyond just restating its topic '
                'or giving an empty label? A requested paragraph may '
                'continue after this sentence.'}
            if re.search(r'\b(?:one|single|a) (?:short |complete )?sentence\b',
                         question, flags=re.I):
                questions['complete_answer_' + str(ord(char))] = {
                    'type': 'noul', 'instructions':
                    'If exact_prefix ' + token + ' were the ONLY sentence, '
                    'would it actually and completely answer user_question '
                    'with the required fact or explanation?'}
    if current_word and any(char in ' .?!,;:' for char in by_char):
        word = current_word.group()
        questions['word_closed'] = {'type': 'noul', 'instructions':
            'If exact_committed_answer ends the current word here, is the '
            'last word ' + json.dumps(word) +
            ' correctly spelled and able to stand before a space or mark? '
            'A single-letter English article or pronoun can be valid.'}
        questions['word_isolated'] = {'type': 'noul', 'instructions':
            'Can exact_word ' + json.dumps(word) +
            ' stand as a correctly spelled English word before a space?'}
        if ' ' in by_char:
            if previous_words:
                questions['adjacent_words'] = {'type': 'noul',
                    'instructions':
                    'If ONE SPACE closes final_word ' + json.dumps(word) +
                    ', can the last two closed words ' +
                    json.dumps(previous_words[-1] + ' ' + word) +
                    ' occur directly adjacent in this exact answer prefix '
                    'with their current grammatical roles? No missing word '
                    'may be inserted between them. A later word can still '
                    'follow and finish an incomplete phrase. Judge adjacency '
                    'in the full prefix, not whether it is a full sentence.'}
                questions['prior_requirement'] = {'type': 'noul',
                    'instructions':
                    'If final_word ' + json.dumps(word) +
                    ' is now ended with a space, does it satisfy the '
                    'grammatical requirement imposed by the immediately '
                    'preceding word ' + json.dumps(previous_words[-1]) +
                    ' in the existing prefix? An article may be followed '
                    'by a modifier or noun; a verb cannot fill a missing '
                    'noun before itself. A later word cannot be inserted '
                    'between these adjacent words.'}
            questions['truthful_word'] = {'type': 'noul',
                'instructions':
                'If ONE SPACE permanently closes the final word of '
                'exact_committed_answer, is the resulting prefix still '
                'capable of becoming a truthful, informative, relevant '
                'answer to user_question by appending characters only? '
                'Function words and unfinished phrases are allowed if '
                'later words can complete them. Judge what was actually '
                'written; do not rewrite earlier words.'}
            questions['closed_context'] = {'type': 'noul', 'instructions':
                'After appending ONE SPACE to exact_committed_answer, do all '
                'already closed words occur in a syntactic order that can '
                'still become a grammatical and relevant answer to '
                'user_question by appending later words? A missing future '
                'noun or verb is acceptable; an already wrong word role is '
                'not. Do not mentally insert or change earlier words.'}
            questions['closed_role'] = {'type': 'noul', 'instructions':
                'If final_word ' + json.dumps(word) + ' is ended by a space '
                'now, does THAT word fit the grammatical role required by '
                'the preceding words in exact_committed_answer? A future '
                'word may complete an unfinished phrase, but cannot go '
                'before final_word or change its role.'}
            questions['closed_irreparable'] = {'type': 'noul', 'instructions':
                'If a space permanently ends final_word ' + json.dumps(word) +
                ' now, would the existing closed words have a word-order '
                'or factual error that NO later appended words can repair? '
                'A merely unfinished phrase is not an error.'}
    answers = model.ask({'user_question': question,
                         'exact_committed_answer': text}, questions)
    closed = (max(probability(answers[key]['noul'])
                  for key in ('word_closed', 'word_isolated'))
              if 'word_closed' in questions else None)
    context = (probability(answers['closed_context']['noul'])
               if 'closed_context' in questions else None)
    role = (probability(answers['closed_role']['noul'])
            if 'closed_role' in questions else None)
    irreparable = (probability(answers['closed_irreparable']['noul'])
                   if 'closed_irreparable' in questions else None)
    adjacent = (probability(answers['adjacent_words']['noul'])
                if 'adjacent_words' in questions else None)
    requirement = (probability(answers['prior_requirement']['noul'])
                   if 'prior_requirement' in questions else None)
    truthful = (probability(answers['truthful_word']['noul'])
                if 'truthful_word' in questions else None)
    current_complete = (probability(answers['current_word_complete']['noul'])
                        if 'current_word_complete' in questions else None)
    scores = {}
    details = {}
    for char, index in by_char.items():
        viability = probability(answers['viable_' + str(ord(char))]['noul'])
        clause = (probability(answers['clause_' + str(ord(char))]['noul'])
                  if char in '.?!;:' else None)
        content = (probability(answers['content_' + str(ord(char))]['noul'])
                   if char in '.?!' else None)
        full_answer = (probability(answers['complete_answer_' + str(ord(char))]['noul'])
                       if 'complete_answer_' + str(ord(char)) in questions else None)
        single_word = (probability(answers['single_word_prefix_' + str(ord(char))]['noul'])
                       if 'single_word_prefix_' + str(ord(char)) in questions else None)
        closes_word = bool(current_word and char in ' .?!,;:')
        rejected = None
        if closes_word and closed < .68:
            rejected = 'closed_word'
        if rejected is None and char == ' ' and context is not None and context < .62:
            rejected = 'closed_word_context'
        # The low grammar score also appears on incomplete but repairable
        # prepositional phrases. An already closed article has a narrower,
        # irreversible requirement: its following word must be nominal.
        article_before = bool(previous_words and
                              previous_words[-1].casefold() in ('a', 'an', 'the'))
        if (rejected is None and char == ' ' and article_before and
                requirement is not None and requirement < .25):
            rejected = 'invalid_prior_requirement'
        if rejected is None and char == ' ' and truthful is not None and truthful < .40:
            rejected = 'untruthful_closed_word'
        if (rejected is None and char == ' ' and role is not None and
                role < .60 and irreparable > .62):
            rejected = 'irreparable_closed_role'
        if clause is not None and clause < .72:
            rejected = 'incomplete_clause'
        if content is not None and content < .65:
            rejected = 'empty_sentence'
        if full_answer is not None and full_answer < .60:
            rejected = 'incomplete_single_sentence'
        if (rejected is None and char.isalpha() and
                text.endswith(char * 3)):
            rejected = 'irreparable_letter_run'
        if (rejected is None and single_word is not None and
                current_complete is not None and current_complete >= .85 and
                single_word < .55):
            rejected = 'joined_or_misspelled_word'
        # An apostrophe cannot be removed after it is committed.
        if char == "'" and (not current_word or "'" in current_word.group()):
            rejected = 'invalid_apostrophe'
        details[char] = {'prefix_viability': viability,
                         'closed_word': closed if closes_word else None,
                         'closed_context': context if char == ' ' else None,
                         'closed_role': role if char == ' ' else None,
                         'closed_irreparable': irreparable if char == ' ' else None,
                         'adjacent_words': adjacent if char == ' ' else None,
                         'prior_requirement': requirement if char == ' ' else None,
                         'article_before': article_before if char == ' ' else None,
                         'truthful_word': truthful if char == ' ' else None,
                         'closed_clause': clause, 'reject': rejected,
                         'sentence_content': content,
                         'single_sentence_answer': full_answer,
                         'current_word_complete': current_complete,
                         'single_word_prefix': single_word,
                         'speculative_rank': rank.index(index)}
        if rejected is None:
            # Viability is a soft reranking signal even at the visible step.
            # Hard checks apply only to committed word/clause boundaries.
            scores[char] = (len(rank) - rank.index(index)) / len(rank) * (
                .45 + .55 * viability)
    if not scores:
        raise RuntimeError('No safe one-character append at a word/clause boundary')
    if (current_word and len(current_word.group()) >= 6 and
            max(details[char]['prefix_viability'] for char in scores) < .35):
        options = {
            'NO_CONTINUATION': 'Every possible append-only completion of the exact committed prefix is now wrong.',
            'CONTINUE': 'At least one append-only completion can still correctly answer the question.'}
        verdict = model.ask({'user_question': question,
                             'exact_committed_answer': text}, {'can_continue': {
            'type': 'choice', 'instructions':
            'Judge the EXACT committed answer so far. Is there a plausible '
            'correct completion by appending new characters only? Do not '
            'rewrite or delete text. An unfinished final word is allowed.',
            'criteria': options}})
        selected, _ = choice(verdict['can_continue'], options)
        if selected == 'NO_CONTINUATION':
            raise RuntimeError('Jev found no viable append-only continuation')
    return max(scores, key=scores.get), {'options': details, 'scores': scores}


def should_stop(model, question, text, log):
    if not text:
        return False
    state = {'user_question': question, 'exact_answer': text}
    options = {'STOP': 'The exact answer is complete and correct; output nothing more.',
               'CONTINUE': 'An essential letter, word, fact, or sentence is still missing.'}
    response = model.ask(state, {
        'finish': {'type': 'choice', 'instructions':
            'Should this exact answer end now? Respect the form requested in the question, including when a short word or number alone is sufficient.',
            'criteria': options},
        'fact': {'type': 'noul', 'instructions':
            'Does exact_answer fully and correctly answer user_question as written, with no missing essential fact?'},
        'form': {'type': 'noul', 'instructions':
            'Does exact_answer satisfy the response form requested by user_question, including its sentence count if specified?'},
        'last_word': {'type': 'noul', 'instructions':
            'Does exact_answer end on a complete, correctly spelled word, number, or punctuation mark, not inside an unfinished word?'},
    })
    selected, probs = choice(response['finish'], options)
    fact, form, boundary = [probability(response[key]['noul']) for key in ('fact', 'form', 'last_word')]
    stop = selected == 'STOP' and fact >= .6 and form >= .7 and boundary >= .9
    if log is not None:
        log({'stop_check': text, 'selected': selected, 'stop_probability': probs['STOP'],
             'fact': fact, 'form': form, 'last_word': boundary, 'stop': stop})
    return stop


def generate(model, question, length=20, width=6, depth=5, log=None, initial_prefix=''):
    text = initial_prefix
    completed = False
    reason = 'character_budget'
    used = 0
    if not text and length > 0:
        chars = candidate_chars('')
        options = {str(index): 'The answer starts with character ' + json.dumps(char) + '.'
                   for index, char in enumerate(chars)}
        response = model.ask({'question_to_answer': question}, {'first_key': {
            'type': 'choice',
            'instructions': ('Identify a direct factually correct response to question_to_answer in the requested form. '
                             'Which is its first character? Do not start with an article or repeat the question.'),
            'criteria': options}})
        selected, first_probs = choice(response['first_key'], options)
        first_options = sorted(first_probs, key=first_probs.get, reverse=True)[:6]
        viable_questions = {key: {'type': 'noul', 'instructions':
            'Can exact_prefix ' + json.dumps(chars[int(key)]) +
            ' be completed by appending characters only into a correct answer '
            'to user_question? The word has only its first character.'}
            for key in first_options}
        viability = model.ask({'user_question': question}, viable_questions)
        first_scores = {key: first_probs[key] *
                        (.45 + .55 * probability(viability[key]['noul']))
                        for key in first_options}
        text = chars[int(max(first_scores, key=first_scores.get))]
        used = 1
        if log is not None:
            log({'event': 'character', 'before': '', 'after': text, 'character': text})
            log({'event': 'first_key', 'selected': text,
                 'first_choice': chars[int(selected)],
                 'viability_scores': first_scores})
        print(repr(text), flush=True)
    for step in range(length - used):
        if should_stop(model, question, text, log):
            completed = True
            reason = 'model_stop'
            break
        paths = [(text, 1., False)]
        for layer in range(depth):
            paths, detail = expansions(model, question, paths)
            paths.sort(key=lambda row: row[1], reverse=True)
            # Distinct possible first characters must survive even when their
            # local probabilities differ. The model later grades deeper paths.
            selected = []
            roots = set()
            for path in paths:
                if len(path[0]) <= len(text):
                    continue
                root = path[0][len(text)]
                if root not in roots:
                    selected.append(path)
                    roots.add(root)
                    if len(selected) == min(width - 2, 6):
                        break
            for path in paths:
                if len(selected) == width:
                    break
                if len(path[0]) <= len(text):
                    continue
                if path not in selected:
                    selected.append(path)
            paths = selected
            if not paths:
                raise RuntimeError('No next-character candidate remains')
            if log is not None:
                log({'step': step, 'layer': layer, 'expand': detail,
                     'beam': [(p, round(w, 5), end) for p, w, end in paths]})
        rank, probabilities, quality = grade(model, question, paths, text)
        next_char, commit_detail = choose_commit(model, question, text, paths, rank)
        if log is not None:
            log({'event': 'commit_choice', 'exact_prefix': text,
                 'selected': next_char, **commit_detail})
        before = text
        text += next_char
        assert len(next_char) == 1 and text == before + next_char
        if log is not None:
            log({'event': 'character', 'before': before, 'after': text, 'character': next_char})
            log({'step': step, 'committed': text,
                 'rank': [(paths[k][0], round(probabilities[str(k)], 3),
                           quality[str(k)]) for k in rank]})
        print(repr(text), flush=True)
        if model.calls >= model.limit:
            reason = 'request_budget'
            break
    # A final answer can finish exactly at the character or request boundary.
    if not completed and model.calls < model.limit and should_stop(model, question, text, log):
        completed = True
        reason = 'model_stop'
    return {'text': text, 'completed': completed, 'reason': reason}


class LimitedJev(Jev):
    def __init__(self, key, limit, max_seconds=600):
        super().__init__(key, max_cost=.10)
        self.limit = limit
        self.max_attempts = 2 * limit
        self.deadline = time.monotonic() + max_seconds
        self.started = time.monotonic()
        self.attempts = 0
        self.http_errors = 0
        self.audit = None

    def ask(self, state, questions):
        if self.calls >= self.limit or self.attempts >= self.max_attempts:
            raise RuntimeError('Request budget reached')
        if time.monotonic() >= self.deadline or self.cost >= self.max_cost:
            raise RuntimeError('Time or reported cost limit reached')
        client = self.clients.get()
        try:
            for retry in range(6):
                if time.monotonic() >= self.deadline:
                    raise RuntimeError('Time limit reached')
                if self.attempts >= self.max_attempts:
                    raise RuntimeError('Request-attempt budget reached')
                self.attempts += 1
                try:
                    response = client.post(ENDPOINT, json={
                        'model': 'typesafe-ai/jev', 'state': state,
                        'questions': questions}, timeout=30,
                        headers={'Authorization': 'Bearer ' + self.key})
                except requests.RequestException as exc:
                    self.http_errors += 1
                    if self.audit:
                        self.audit({'event': 'http_error', 'status': 'network_error',
                                    'retry': retry, 'type': type(exc).__name__})
                    if retry == 5:
                        raise RuntimeError('Network failure after retries') from None
                    time.sleep(min(2 ** retry, 8))
                    continue
                if response.ok:
                    break
                self.http_errors += 1
                if self.audit:
                    self.audit({'event': 'http_error', 'status': response.status_code,
                                'retry': retry})
                if response.status_code not in (503, 504, 529) or retry == 5:
                    raise RuntimeError('Gateway returned HTTP ' + str(response.status_code))
                time.sleep(min(2 ** retry, 8))
            data = response.json()
        finally:
            self.clients.put(client)
        delta = float(data['provider_metadata']['gateway']['cost'])
        if not math.isfinite(delta) or delta < 0:
            raise ValueError('Invalid gateway cost')
        self.calls += 1
        self.cost += delta
        answers = data['answers']
        if set(answers) != set(questions):
            raise ValueError('Missing or extra Jev answers')
        if self.audit:
            self.audit({'event': 'request', 'number': self.calls,
                        'attempts': self.attempts, 'state': state,
                        'questions': questions, 'answers': answers,
                        'reported_cost': delta})
        return answers


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('question')
    parser.add_argument('--length', type=int, default=240)
    parser.add_argument('--width', type=int, default=6)
    parser.add_argument('--depth', type=int, default=3)
    parser.add_argument('--limit', type=int, default=1800)
    parser.add_argument('--max-seconds', type=int, default=1500)
    parser.add_argument('--trace', default='jev-trace.jsonl')
    parser.add_argument('--initial-prefix', default='')
    args = parser.parse_args()
    secret = os.environ.get('AI_GATEWAY_API_KEY')
    if not secret:
        raise RuntimeError('AI_GATEWAY_API_KEY required in environment')
    model = LimitedJev(secret, args.limit, args.max_seconds)
    with open(args.trace, 'x') as stream:
        latest = [args.initial_prefix]
        def log(row):
            if row.get('event') == 'character':
                latest[0] = row['after']
            stream.write(json.dumps(row) + '\n')
            stream.flush()
        model.audit = log
        source = Path(__file__).read_bytes()
        dependency = DEPENDENCY
        Path(args.trace).with_suffix('.py').write_bytes(source)
        log({'event': 'start', 'task': args.question,
             'initial_prefix': args.initial_prefix,
             'source_sha256': hashlib.sha256(source).hexdigest(),
             'keyboard_dependency_sha256': hashlib.sha256(dependency.read_bytes()).hexdigest(),
             'length_limit': args.length, 'request_limit': args.limit,
             'max_attempts': model.max_attempts,
             'max_seconds': args.max_seconds,
             'width': args.width, 'depth': args.depth})
        recoveries = 0
        while True:
            remaining = args.length - (len(latest[0]) - len(args.initial_prefix))
            if remaining <= 0:
                output = {'text': latest[0], 'completed': False, 'reason': 'character_budget'}
                break
            try:
                output = generate(model, args.question, remaining, args.width,
                                  args.depth, log, latest[0])
                break
            except RuntimeError as error:
                reason = str(error)
                if reason in ('Gateway returned HTTP 503', 'Gateway returned HTTP 504',
                              'Gateway returned HTTP 529') and recoveries < 3 and model.calls < model.limit:
                    recoveries += 1
                    log({'event': 'resume', 'exact_prefix': latest[0],
                         'reason': reason, 'recovery': recoveries})
                    continue
                output = {'text': latest[0], 'completed': False, 'reason': reason}
                break
        output.update({'event': 'summary', 'requests': model.calls,
                       'attempts': model.attempts,
                       'http_errors': model.http_errors,
                       'elapsed_seconds': round(time.monotonic() - model.started, 3),
                       'reported_cost_usd': model.cost})
        log(output)
        print(json.dumps(output), flush=True)
