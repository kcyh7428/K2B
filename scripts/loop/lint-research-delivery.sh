#!/usr/bin/env bash
# Flag raw/research/*.md notes > 30 days old with `follow-up-delivery: null`
# or absent. Called from /lint. Stdout = list of flagged paths, one per line.
set -euo pipefail

python3 - <<'PYEOF'
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

research_dir = Path(
    os.environ.get("K2B_LINT_RESEARCH_DIR")
    or (Path.home() / "Projects" / "K2B-Vault" / "raw" / "research")
)
today = date.today()
if not research_dir.is_dir():
    sys.exit(0)

for p in sorted(research_dir.glob("*.md")):
    m = re.match(r"(\d{4}-\d{2}-\d{2})_", p.name)
    if not m:
        continue
    try:
        when = datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        continue
    age_days = (today - when).days
    if age_days <= 30:
        continue
    try:
        head = p.read_text(encoding="utf-8").splitlines()[:40]
    except OSError:
        continue
    in_fm = False
    fm_closed = False
    has_delivery = False
    for i, line in enumerate(head):
        if i == 0 and line.strip() == "---":
            in_fm = True
            continue
        if in_fm and line.strip() == "---":
            fm_closed = True
            break
        if in_fm and re.match(r"^follow-up-delivery:\s*\S", line):
            if "null" not in line.lower():
                has_delivery = True
    if not fm_closed:
        continue
    if has_delivery:
        continue
    print(f"{p.name} (age {age_days} days, follow-up-delivery missing/null)")
PYEOF
