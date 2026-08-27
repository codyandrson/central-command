import json, collections
from litellm.router_strategy.complexity_router.complexity_router import ComplexityRouter
c = json.load(open('/tmp/corpus_lab.json'))
charter = open('/tmp/charter.txt').read()
r = ComplexityRouter(model_name="probe", litellm_router_instance=None)
by = {0: [], 1: []}
for i in c:
    t, s, _ = r.classify(f"Subject: {i['subject']}\n\n{i['text']}", system_prompt=charter)
    by[i['dismissed']].append((t.value, s))
print("ground truth: dismissed = agent judged NO ACTION needed; acted = produced a proposal\n")
for lab, name in ((1,"dismissed (no action)"), (0,"acted on (proposal)")):
    v = by[lab]
    if not v: continue
    d = collections.Counter(t for t,_ in v); n=len(v)
    ss = sorted(s for _,s in v)
    print(f"{name:24} n={n:3}  " + "  ".join(f"{t}:{100.0*d.get(t,0)/n:5.1f}%" for t in ("SIMPLE","MEDIUM","COMPLEX","REASONING")))
    print(f"{'':24}     median score {ss[n//2]:.3f}   mean {sum(ss)/n:.3f}")
