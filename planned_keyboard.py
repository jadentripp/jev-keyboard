"""Jev predicts a word length, then chooses each character; no word examples."""
from jev_keyboard import WordKeyboard


class PlannedKeyboard(WordKeyboard):
    def __init__(self):
        super().__init__()
        self.target_length = None

    def body(self, task):
        body = super().body(task)
        if self.stage != 'word':
            return body
        state = {'task':task, 'answer_so_far':self.draft,
                 'word_number':len(self.completed_words)+1, 'current_word':self.word}
        if self.target_length is None:
            instructions = 'How many characters are in the next word of a good answer?'
            criteria = {str(n):None for n in range(1,33)}
            criteria['longer'] = 'More than 32 characters'
        else:
            state['word_length'] = self.target_length
            state['word_slots'] = self.word + '_' * max(0,self.target_length-len(self.word))
            state['next_position'] = len(self.word)+1
            instructions = f'What is letter {len(self.word)+1} of word {len(self.completed_words)+1} of the answer?'
            criteria = {k:None for k in body['questions']['next_key']['criteria'] if k!='END_WORD'}
            if len(self.word)>=self.target_length:
                criteria['END_WORD'] = 'This word is complete.'
        body['state'] = state
        body['questions']['next_key'] = {'type':'choice','instructions':instructions,'criteria':criteria}
        return body

    def apply(self, action):
        if self.stage == 'word' and self.target_length is None:
            if action not in self.body('')['questions']['next_key']['criteria']:
                raise ValueError('Invalid word-length prediction')
            self.target_length = 33 if action=='longer' else int(action)
            return False
        done = super().apply(action)
        if self.stage == 'route':
            self.target_length = None
        return done
