# scripts/golden/probe_dataset.py

from datasets import load_dataset

ds = load_dataset("ronantakizawa/github-codereview", split="train")

print("language:", ds.unique("language"))
print("repo_language:", ds.unique("repo_language"))
print("comment_type:", ds.unique("comment_type"))

# spot-check a python-looking row end to end
for row in ds:
  if "python" in str(row["language"]).lower() or "python" in str(row["repo_language"]).lower():
    print({k: (str(v)[:120] if isinstance(v, str) else v) for k, v in row.items()})
    break
