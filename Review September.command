#!/bin/zsh
set -e
task_dir="$(cd "$(dirname "$0")" && pwd)"
corrected_dir="$task_dir/reports/audit-replay-20261008T160527-b7a627fe-luna6-xhigh/album-corrected-20261019"
if [[ -f "$corrected_dir/comparison.json" && -f "$corrected_dir/human-review.json" ]]; then
  review_page="$(python3 "$task_dir/manual_review.py" "$corrected_dir/comparison.json" --review "$corrected_dir/human-review.json")"
else
  review_page="$(python3 "$task_dir/manual_review.py")"
fi
if [[ -d "/Applications/Google Chrome.app" ]]; then
  open -a "Google Chrome" "$review_page"
else
  open "$review_page"
fi
echo "Review opened. Decisions save in that browser; export a backup when you pause or finish."
