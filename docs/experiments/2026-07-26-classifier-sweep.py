import json, collections
from litellm.router_strategy.complexity_router.complexity_router import ComplexityRouter

corpus = json.load(open('/tmp/corpus_raw.json'))
charter = open('/tmp/charter.txt').read()
prompts = [f"Subject: {i['subject']}\n\n{i['text']}" for i in corpus]

# Business English that the DEFAULT code list treats as code signal.
CODE_MIN = ["def ", "async", "await", "traceback", "stacktrace", "regex",
            "python", "javascript", "typescript", "sql", "refactor", "compile",
            "git", "docker", "kubernetes", "yaml", "json", "stack trace"]

CONFIGS = {
 "A baseline (out of the box)": {},
 "B code keywords trimmed to real code": {"code_keywords": CODE_MIN},
 "C  B + medium_complex 0.35->0.50": {"code_keywords": CODE_MIN,
      "tier_boundaries": {"simple_medium":0.15,"medium_complex":0.50,"complex_reasoning":0.70}},
 "D  C + simple_medium 0.15->0.30": {"code_keywords": CODE_MIN,
      "tier_boundaries": {"simple_medium":0.30,"medium_complex":0.50,"complex_reasoning":0.70}},
}

for label, cfg in CONFIGS.items():
    r = ComplexityRouter(model_name="probe", litellm_router_instance=None,
                         complexity_router_config=cfg or None)
    tiers=[]; scores=[]
    for p in prompts:
        t,s,_ = r.classify(p, system_prompt=charter)
        tiers.append(t.value); scores.append(s)
    d = collections.Counter(tiers); n=len(tiers); scores.sort()
    line = "  ".join(f"{t}:{100.0*d.get(t,0)/n:4.1f}%" for t in ("SIMPLE","MEDIUM","COMPLEX","REASONING"))
    print(f"{label:38} {line}   median score {scores[n//2]:.3f}")
