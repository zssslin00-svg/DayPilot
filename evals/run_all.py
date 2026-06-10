from __future__ import annotations

import os
from pathlib import Path

os.environ["DAYPILOT_LLM_MODE"] = "mock"

from evals.scripts import (
    run_daily_goal_evals,
    run_difficulty_evals,
    run_feedback_revision_evals,
    run_weekly_report_evals,
    run_workday_policy_evals,
)
from evals.scripts.score_utils import RESULTS_DIR, render_markdown_summary


def run_all() -> list[dict]:
    summaries = [
        run_daily_goal_evals.run(),
        run_feedback_revision_evals.run(),
        run_weekly_report_evals.run(),
        run_difficulty_evals.run(),
        run_workday_policy_evals.run(),
    ]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "regression_summary.md").write_text(
        render_markdown_summary(summaries),
        encoding="utf-8",
    )
    return summaries


def main() -> None:
    summaries = run_all()
    print("DayPilot eval summary")
    for summary in summaries:
        print(
            f"- {summary['suite']}: pass {summary['passed']}/{summary['total']}, "
            f"average {summary['average_score']}"
        )
    print(f"Results written to {Path(RESULTS_DIR).resolve()}")


if __name__ == "__main__":
    main()
