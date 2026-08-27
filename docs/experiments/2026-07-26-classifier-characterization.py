import json, collections, sys
from litellm.router_strategy.complexity_router.complexity_router import ComplexityRouter

corpus = json.load(open('/tmp/corpus_raw.json'))
charter = open('/tmp/charter.txt').read()
r = ComplexityRouter(model_name="probe", litellm_router_instance=None)

rows = []
for item in corpus:
    prompt = f"Subject: {item['subject']}\n\n{item['text']}"
    tier, score, signals = r.classify(prompt, system_prompt=charter)
    rows.append({"id": item["id"], "subject": item["subject"][:70],
                 "tier": tier.value, "score": round(score, 4), "signals": signals,
                 "chars": len(item["text"])})

dist = collections.Counter(x["tier"] for x in rows)
print("=== TIER DISTRIBUTION (n=%d real emails, charter as system_prompt) ===" % len(rows))
for t in ("SIMPLE","MEDIUM","COMPLEX","REASONING"):
    n = dist.get(t,0)
    print(f"  {t:10} {n:4}  {100.0*n/len(rows):5.1f}%  {'#'*int(40.0*n/len(rows))}")
scores=[x["score"] for x in rows]
scores.sort()
print("\nscore  min %.3f  p25 %.3f  median %.3f  p75 %.3f  max %.3f" % (
    scores[0], scores[len(scores)//4], scores[len(scores)//2], scores[3*len(scores)//4], scores[-1]))
print("boundaries: simple<0.15<=medium<0.35<=complex<0.60<=reasoning")
print("\n=== signal frequency ===")
sig = collections.Counter(s for x in rows for s in x["signals"])
for s,n in sig.most_common(12): print(f"  {n:4}  {s}")
json.dump(rows, open('/tmp/results.json','w'), indent=1)
