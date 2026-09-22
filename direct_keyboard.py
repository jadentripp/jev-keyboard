"""Ask the user's question directly over single-character prefix alternatives."""
import json
from jev_keyboard import WordKeyboard
import math


class DirectKeyboard(WordKeyboard):
    def __init__(self):
        super().__init__()
        self.actions = {}

    def body(self, task):
        body = super().body(task)
        if self.stage == 'route':
            descriptions = {
                'WORD':'Start spelling the next word.',
                'SPACE':'Append one space.',
                'PUNCTUATION':'Select a punctuation character.',
                'DONE':'The response fully satisfies the task, including its requested format.'}
            body['questions']['next_key']['criteria'] = {
                k:descriptions[k] for k in body['questions']['next_key']['criteria']}
        if self.stage != 'word':
            return body
        raw = body['questions']['next_key']['criteria']
        criteria = {}
        self.actions = {}
        for i, action in enumerate(raw):
            key = 'key_' + str(i)
            self.actions[key] = action
            prefix = self.draft + (' ' if action=='END_WORD' else action)
            criteria[key] = 'An answer beginning with ' + json.dumps(prefix)
            if action=='END_WORD':
                criteria[key] += ' (end of this word)'
        body['state'] = {'answer_so_far':self.draft}
        body['questions']['next_key'] = {'type':'choice','instructions':task,'criteria':criteria}
        return body

    def apply(self, key):
        if self.stage != 'word':
            return super().apply(key)
        self.body('')
        if key not in self.actions:
            raise ValueError('Unknown character option')
        action = self.actions[key]
        if action=='END_WORD':
            self.completed_words.append(self.word)
            self.text += self.word
            self.word = ''
            self.stage = 'route'
        else:
            assert len(action)==1
            self.word += action
        return False


class BoundaryKeyboard(DirectKeyboard):
    """Separate word completion from next-character classification."""
    def body(self, task):
        body = super().body(task)
        if self.stage == 'word' and self.word:
            body['state']['task'] = task
            end_key = next(k for k,v in self.actions.items() if v=='END_WORD')
            body['questions']['next_key']['criteria'].pop(end_key)
            body['questions']['word_complete'] = {'type':'noul',
                'instructions':'Is '+json.dumps(self.word)+' a fully spelled word in a correct answer to the task?'}
        return body

    def select_answer(self, answer, response):
        if self.stage != 'word' or not self.word:
            return answer
        score = response['answers']['word_complete']['noul']
        if not isinstance(score,(int,float)) or not math.isfinite(score) or not 0<=score<=1:
            raise ValueError('Invalid word-completion probability')
        choice = answer['choice']
        if score>=.5:
            choice = next(k for k,v in self.actions.items() if v=='END_WORD')
        return {'choice':choice,'selection_method':'word_complete_then_character',
                'word_complete':score,'character_choice':answer['choice']}
