"""Score one-character continuations independently; never propose whole words."""
import json
from pathlib import Path
import time
from jev_keyboard import MODEL, CHARS, evaluate


def body(task, draft):
    actions = [c for c in CHARS if c.isalnum()]
    questions = {}
    for n, char in enumerate(actions):
        questions[str(n)] = {
            'type':'noul',
            'instructions':'Could ' + json.dumps(draft+char) + ' naturally begin a correct answer?',
        }
    return {'model':MODEL,'state':{'task':task},'questions':questions}, actions


def probe(task,draft,key,path):
    request, actions = body(task,draft)
    tick = time.monotonic()
    response = evaluate(request,key)
    record = {'request':request,'response':response,'latency_seconds':time.monotonic()-tick}
    Path(path).write_text(json.dumps(record,indent=2)+'\n')
    ranked = sorted([(c,response['answers'][str(n)]['noul']) for n,c in enumerate(actions)],key=lambda x:-x[1])
    print(json.dumps({'draft':draft,'top':ranked[:12]}),flush=True)
    return ranked
