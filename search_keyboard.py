"""Character-only lookahead using Jev's own predictions. No example words."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import getpass
import json
import math
import os
from pathlib import Path
from threading import Lock
import time

from direct_keyboard import BoundaryKeyboard
from jev_keyboard import MODEL, GatewayError, evaluate, parse_answer


def generate_search(task, key, trace_path, *, max_steps=250, max_seconds=1800,
                    max_cost=.10, stop_file=None, resume_trace=None, on_change=None,
                    threshold=.55, width=4, min_chars=25, max_chars=50,
                    case_gate=True, conditional_case=True, boundary_threshold=.4,
                    boundary_mode='context',
                    require_punctuation=True, call=evaluate):
    if not 1 <= min_chars <= max_chars:
        raise ValueError('Invalid character limits')
    keyboard = BoundaryKeyboard()
    if resume_trace:
        seen = set()
        def replay(path):
            path = Path(path).resolve()
            if path in seen:
                raise ValueError('Cycle in trace history')
            seen.add(path)
            rows = [json.loads(s) for s in path.read_text().splitlines()]
            start = rows[0]
            if start.get('mode')!='search' or start.get('task')!=task:
                raise ValueError('Resume task or mode differs')
            if start.get('resume_source'):
                replay(path.parent/start['resume_source'])
            if keyboard.draft!=start['initial_draft']:
                raise ValueError('Invalid starting prefix')
            for row in rows:
                if row.get('event')!='key':
                    continue
                if keyboard.draft!=row['before']:
                    raise ValueError('Discontinuous trace')
                keyboard.body(task)
                keyboard.apply(row['selection']['choice'])
                if keyboard.draft!=row['after']:
                    raise ValueError('Recorded action differs from text')
        replay(resume_trace)
    started = time.monotonic()
    lock = Lock()
    stats = {'requests':0,'questions':0,'input_tokens':0,'output_tokens':0,
             'reported_cost_usd':0.,'request_outcome_unknown':False}
    reason, error, count, searches = 'max_steps', None, 0, 0
    with Path(trace_path).open('x') as log:
        def record(event):
            with lock:
                log.write(json.dumps(event,ensure_ascii=False)+'\n')
                log.flush()

        def request(body, purpose):
            if stop_file and Path(stop_file).exists():
                raise GatewayError('Cancelled between requests')
            if time.monotonic()-started>=max_seconds:
                raise GatewayError('Time limit reached')
            with lock:
                if stats['reported_cost_usd']>=max_cost:
                    raise GatewayError('Reported cost limit reached')
            tick = time.monotonic()
            try:
                response = call(body,key)
            except BaseException:
                with lock:
                    stats['request_outcome_unknown'] = True
                raise
            cost = response.get('provider_metadata',{}).get('gateway',{}).get('cost')
            with lock:
                stats['requests'] += 1
                stats['questions'] += len(body['questions'])
                for name in ('input_tokens','output_tokens'):
                    stats[name] += response.get('usage',{}).get(name,0)
                if cost is not None:
                    cost = float(cost)
                    if not math.isfinite(cost) or cost<0:
                        raise GatewayError('Invalid cost metadata')
                    stats['reported_cost_usd'] += cost
            record({'event':'request','purpose':purpose,'request':body,'response':response,
                    'latency_seconds':time.monotonic()-tick})
            if cost is None:
                raise GatewayError('Missing cost metadata')
            return response

        def decide(board, purpose):
            body = board.body(task)
            body['state']['task'] = task
            if board.stage == 'route':
                body['questions']['next_key']['instructions'] = 'Continue this sentence.'
                body['state']['length_requirement'] = {'min_characters':min_chars,'max_characters':max_chars}
            if board.stage == 'route' and (len(board.draft) < min_chars or
                    (require_punctuation and not board.draft.rstrip().rstrip('\"\u201d\u2019)').endswith(('.', '!', '?')))):
                body['questions']['next_key']['criteria'].pop('DONE', None)
            if board.stage == 'word' and case_gate:
                body['questions']['letter_case'] = {'type':'choice',
                    'instructions':'What kind of character comes next in the answer?',
                    'criteria':{'lower':'Lowercase letter.','upper':'Uppercase letter.',
                                'digit':'Digit.'}}
            if board.stage == 'word' and case_gate and conditional_case:
                control_body = deepcopy(body)
                control_body['questions'].pop('next_key')
                if board.word and boundary_mode == 'blend':
                    control_body['questions']['spelling_complete'] = {'type':'noul',
                        'instructions':'Is the last word a complete English word?'}
                control = request(control_body,purpose+'_case_and_boundary')
                if board.word:
                    scores = [control['answers']['word_complete']['noul']]
                    if boundary_mode == 'blend':
                        scores.append(control['answers']['spelling_complete']['noul'])
                    if any(not isinstance(s,(int,float)) or not math.isfinite(s) or not 0<=s<=1 for s in scores):
                        raise ValueError('Invalid word boundary score')
                    if sum(scores)/len(scores) >= boundary_threshold:
                        end = next(k for k,v in board.actions.items() if v=='END_WORD')
                        return {'choice':end,'selection_method':'word_boundary'}, {'probabilities':{}}
                kind = parse_answer({'answers':{'next_key':control['answers']['letter_case']}},
                                    control_body['questions']['letter_case']['criteria'])['choice']
                predicate = {'lower':str.islower,'upper':str.isupper,'digit':str.isdigit}[kind]
                body['questions'] = {'next_key':body['questions']['next_key']}
                body['questions']['next_key']['criteria'] = {
                    k:v for k,v in body['questions']['next_key']['criteria'].items()
                    if predicate(board.actions[k])}
                response = request(body,purpose+'_character')
                raw = parse_answer(response,body['questions']['next_key']['criteria'])
                return raw,raw
            response = request(body,purpose)
            raw = parse_answer(response,body['questions']['next_key']['criteria'])
            selected = board.select_answer(raw,response)
            if board.stage == 'word' and case_gate and board.actions[selected['choice']] != 'END_WORD':
                kind_answer = parse_answer({'answers':{'next_key':response['answers']['letter_case']}},
                                          body['questions']['letter_case']['criteria'])
                kind = kind_answer['choice']
                predicates = {'lower':str.islower,'upper':str.isupper,'digit':str.isdigit}
                allowed = {k:p for k,p in raw['probabilities'].items()
                           if predicates[kind](board.actions[k])}
                choice = max(allowed,key=allowed.get)
                selected = {**selected,'choice':choice,'selection_method':'case_then_character'}
                raw = {**raw,'choice':choice,'probabilities':allowed}
            return selected,raw

        record({'event':'start','mode':'search','task':task,'initial_draft':keyboard.draft,
                'resume_source':os.path.relpath(resume_trace,Path(trace_path).parent) if resume_trace else None,
                'width':width,'lookahead_actions':1,'threshold':threshold,
                'min_chars':min_chars,'max_chars':max_chars,'case_gate':case_gate,
                'require_punctuation':require_punctuation,
                'conditional_case':conditional_case,'boundary_threshold':boundary_threshold,
                'boundary_mode':boundary_mode,
                'routing_prompt':'Continue this sentence.',
                'max_steps':max_steps,'max_seconds':max_seconds,'max_cost_usd':max_cost,
                'proposal_source':'Only Jev character predictions; no external word candidates'})
        try:
            for step in range(max_steps):
                before = keyboard.draft
                selected,raw = decide(keyboard,'next_action')
                chosen = selected['choice']
                method = selected.get('selection_method','choice')
                is_letter = keyboard.stage=='word' and keyboard.actions.get(chosen)!='END_WORD'
                if is_letter and raw['probabilities'][chosen]<threshold:
                    root_keys = sorted(raw['probabilities'],key=raw['probabilities'].get,reverse=True)[:width]
                    def advance(root_key):
                        trial = deepcopy(keyboard)
                        trial.apply(root_key)
                        middle = trial.draft
                        proposal,_ = decide(trial,'speculative_next_action')
                        trial.apply(proposal['choice'])
                        assert trial.draft.startswith(middle) and len(trial.draft)-len(middle) in (0,1)
                        return {'root_key':root_key,'next_key':proposal['choice'],'text':trial.draft}
                    with ThreadPoolExecutor(max_workers=width) as pool:
                        branches = list(pool.map(advance,root_keys))
                    ranking = {'model':MODEL,'state':{'task':task,'committed_prefix':before},'questions':{
                        'next_key':{'type':'choice',
                                    'instructions':'Which continuation is most grammatical and relevant to the task?',
                                    'criteria':{str(i):b['text'] for i,b in enumerate(branches)}}}}
                    judged = request(ranking,'rank_model_generated_continuations')
                    ranking_answer = parse_answer(judged,ranking['questions']['next_key']['criteria'])
                    chosen = branches[int(ranking_answer['choice'])]['root_key']
                    record({'event':'lookahead','step':step,'branches':branches,
                            'selected_branch':int(ranking_answer['choice']),
                            'committed_action':chosen,'future_actions_discarded':True})
                    method = 'lookahead'
                    searches += 1
                decoded = keyboard.actions[chosen] if keyboard.stage=='word' else chosen
                done = keyboard.apply(chosen)
                assert keyboard.draft.startswith(before) and len(keyboard.draft)-len(before) in (0,1)
                count += 1
                record({'event':'key','step':step,'before':before,'after':keyboard.draft,
                        'selection':{'choice':chosen,'decoded_action':decoded,'method':method}})
                if on_change:
                    on_change(keyboard.draft,decoded)
                if done:
                    reason = 'done'
                    break
                if len(keyboard.draft) >= max_chars and keyboard.stage != 'route':
                    reason = 'character_limit'
                    break
                if len(keyboard.draft) > max_chars:
                    reason = 'character_limit'
                    break
                if len(keyboard.draft)>=12 and len(set(keyboard.draft[-12:]))==1:
                    reason = 'repetition'
                    break
                if len(keyboard.completed_words)>=4 and len({w.casefold() for w in keyboard.completed_words[-4:]})==1:
                    reason = 'repeated_words'
                    break
        except (GatewayError,ValueError,KeyError,TypeError) as exc:
            reason,error = 'error',str(exc)
        except KeyboardInterrupt:
            reason = 'interrupted'
        summary = {'event':'summary','mode':'search','task':task,'text':keyboard.draft,
                   'stop_reason':reason,'completed':reason=='done','error':error,'steps':count,
                   'character_count':len(keyboard.draft),
                   'length_ok':min_chars<=len(keyboard.draft)<=max_chars,
                   'lookahead_decisions':searches,'elapsed_seconds':time.monotonic()-started,**stats}
        record(summary)
        return summary


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task')
    parser.add_argument('--trace',type=Path,default=Path('search-'+str(time.time_ns())+'.jsonl'))
    parser.add_argument('--resume-trace',type=Path)
    parser.add_argument('--stop-file',type=Path)
    parser.add_argument('--min-chars',type=int,default=25)
    parser.add_argument('--max-chars',type=int,default=50)
    args = parser.parse_args()
    key = os.environ.get('AI_GATEWAY_API_KEY') or getpass.getpass('AI Gateway key (not saved): ')
    result = generate_search(args.task,key,args.trace,resume_trace=args.resume_trace,stop_file=args.stop_file,
                             min_chars=args.min_chars,max_chars=args.max_chars,
                             on_change=lambda text,action:print(repr(text),action,flush=True))
    print(json.dumps(result,indent=2))
