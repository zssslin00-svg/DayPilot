# DayPilot Eval Runbook

Run all MVP evals from an Anaconda Prompt:

```bat
cd /d D:\path\to\DayPilot
python -m evals.run_all
```

Outputs are written to:

- `evals\results\daily_goal_results.json`
- `evals\results\feedback_revision_results.json`
- `evals\results\weekly_report_results.json`
- `evals\results\difficulty_results.json`
- `evals\results\workday_policy_results.json`
- `evals\results\regression_summary.md`

The cases are synthetic and versioned under `evals\cases`. Do not put raw personal check-in data into eval cases; convert real issues into small anonymized cases first.
