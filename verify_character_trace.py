"""Audit committed character events. Semantic correctness requires separate review."""
import argparse
import json
from pathlib import Path


def verify_trace(path, seen=None):
    path = Path(path).resolve()
    seen = set() if seen is None else seen
    if path in seen:
        raise ValueError('Cycle in trace ancestry')
    seen.add(path)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    start = rows[0]
    if start['event'] != 'start':
        raise ValueError('Missing trace start')
    if start.get('mode') != 'search':
        raise ValueError('This auditor expects a search-mode trace')
    text = ''
    characters = 0
    actions = 0
    if start.get('resume_source'):
        parent = verify_trace(path.parent / start['resume_source'], seen)
        if parent['task'] != start['task']:
            raise ValueError('Task changed while resuming the trace')
        text = parent['text']
        characters = parent['character_appends']
        actions = parent['actions']
    if text != start.get('initial_draft', ''):
        raise ValueError('Starting text has no recorded generation history')
    for row in rows:
        if row['event'] != 'key':
            continue
        before, after = row['before'], row['after']
        if before != text or not after.startswith(before):
            raise ValueError('Text was deleted, replaced, or inserted without an action')
        appended = after[len(before):]
        if len(appended) > 1:
            raise ValueError('An action appended more than one character')
        selection = row.get('selection', {})
        decoded = selection.get('decoded_action')
        if decoded and appended:
            literal = {'SPACE':' ', 'ENTER':'\n'}.get(decoded, decoded)
            if literal != appended:
                raise ValueError('Text differs from the recorded model action')
        if decoded in ('DELETE', 'BACKSPACE'):
            raise ValueError('Forbidden deletion action')
        characters += len(appended)
        actions += 1
        text = after
    summary = rows[-1] if rows[-1]['event'] == 'summary' else {}
    if summary and summary['text'] != text:
        raise ValueError('Summary does not match the character trace')
    return {
        'trace':path.name, 'task':start['task'], 'text':text, 'characters':len(text),
        'character_appends':characters, 'actions':actions,
        'append_only_one_character':True,
        'started_empty':True,
        'length_ok':start.get('min_chars',25)<=len(text)<=start.get('max_chars',50),
        'ends_with_sentence_punctuation':text.rstrip().rstrip('\"\u201d\u2019)').endswith(('.', '!', '?')),
        'model_selected_done':summary.get('completed',False),
        'semantic_correctness':'Requires independent review; these checks do not establish it.'
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace',type=Path)
    print(json.dumps(verify_trace(parser.parse_args().trace),indent=2))
