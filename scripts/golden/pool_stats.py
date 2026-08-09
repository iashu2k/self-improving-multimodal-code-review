# scripts/golden/pool_stats.py
import json
from collections import Counter
from pathlib import Path

cands = [
    json.loads(line)
    for line in Path("data/golden_prs/candidates/candidates.jsonl").read_text().splitlines()
]
pos = [c for c in cands if not c["is_negative"]]
neg = [c for c in cands if c["is_negative"]]

print("positives by type:", Counter(c["comment_type"] for c in pos))
terse = [
    c
    for c in pos
    if c["reviewer_comment"].lstrip().startswith("```suggestion")
    and len(c["reviewer_comment"]) < 200
]
print(f"terse suggestion-only comments: {len(terse)}/{len(pos)}")
print("  (harder to annotate - keep, but expect culls)")
print("negatives by repo diversity:", len({c["repo_name"] for c in neg}))
print("top repos:", Counter(c["repo_name"] for c in cands).most_common(8))
