"""Single-character prompt comparisons; no vocabulary or supplied answers."""
import json
from pathlib import Path
import string
import time

from jev_keyboard import evaluate, MODEL, CHARS, parse_answer


VARIANTS = {
    'prefix': 'Which is the correct next prefix of the answer?',
    'continue': 'Continue the answer. Which character comes next?',
    'word': 'Which letter comes next in the first word of a correct answer?',
    'viable': 'Which unfinished answer can be continued into a correct response?',
}


def probe(task, key, path, steps=10):
    drafts = {name:'' for name in VARIANTS}
    finished = set()
    with Path(path).open('x') as log:
        for step in range(steps):
            questions = {}
            for name, prompt in VARIANTS.items():
                if name in finished:
                    continue
                draft = drafts[name]
                criteria = {c:draft+c for c in CHARS if c.isalnum()}
                if draft:
                    criteria['END_WORD'] = draft + ' (complete word)'
                questions[name] = {'type':'choice', 'instructions':prompt,
                                   'criteria':criteria}
            if not questions:
                break
            body = {'model':MODEL,'state':{'task':task},'questions':questions}
            tick = time.monotonic()
            response = evaluate(body,key)
            log.write(json.dumps({'event':'request','step':step,'before':drafts.copy(),
                                  'request':body,'response':response,'latency_seconds':time.monotonic()-tick})+'\n')
            log.flush()
            for name, question in questions.items():
                answer = parse_answer({'answers':{'next_key':response['answers'][name]}},question['criteria'])
                action = answer['choice']
                if action == 'END_WORD':
                    finished.add(name)
                else:
                    assert len(action) == 1
                    drafts[name] += action
            print(json.dumps({'step':step,'drafts':drafts,'finished':sorted(finished)}),flush=True)
        log.write(json.dumps({'event':'summary','task':task,'drafts':drafts,'finished':sorted(finished)})+'\n')
    return drafts
