import json

with open("logs/audit_log.json") as f:
    data = json.load(f)

print(f"\nTotal audit records: {len(data)}")
print("-" * 80)
statuses = {}
for r in data:
    s = r["status"]
    statuses[s] = statuses.get(s, 0) + 1
    tools_str = ", ".join(r["tools_used"])
    print(f"  {r['ticket_id']:10} | {r['status']:10} | conf={r['confidence']:.2f} | steps={len(r['steps']):2} | [{tools_str}]")

print("-" * 80)
print(f"Status breakdown: {statuses}")
avg_conf = sum(r["confidence"] for r in data) / len(data)
avg_steps = sum(len(r["steps"]) for r in data) / len(data)
print(f"Avg confidence: {avg_conf:.2%}  |  Avg steps: {avg_steps:.1f}")
