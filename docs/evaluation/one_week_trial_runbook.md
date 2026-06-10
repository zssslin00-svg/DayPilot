# DayPilot One-Week Trial Runbook

This runbook supports the first real DayPilot MVP trial. It does not invent trial feedback; use it only to record actual use and decide the smallest repair loop after the week.

## Goal

Validate the MVP loop:

1. Workday morning goal generation.
2. Optional online goal revision.
3. Evening three-field check-in.
4. Friday weekly report generation.
5. weekly_focus handoff into the following Monday goal.

## Daily Routine

Each workday:

1. Open DayPilot and read the generated goal.
2. Record whether the goal is clear enough to start within 5 minutes.
3. If the goal is wrong, use online feedback and record the before/after goal.
4. Do the work against the active goal version.
5. Submit check-in with completion text, felt difficulty `1-5`, and optional tomorrow direction.
6. Record friction in `exports\one_week_trial_log_template.md`.

On Friday:

1. Submit Friday check-in first.
2. Generate the weekly report.
3. Check whether the report has three sections, avoids daily-log style, and gives result-oriented next-week plans.
4. Check whether saved weekly_focus would help next Monday.

## Effectiveness Criteria

- At least 4 of 5 initial goals are clear enough to start within 5 minutes, or the feedback revision makes them usable.
- Every goal has one main objective, clear completion criteria, a minimum acceptable result, and a boundary for what not to do.
- At least one online feedback revision visibly changes scope, time, goal type, or completion criteria.
- Difficulty feels broadly calibrated by Friday: not repeatedly too large, too easy, or directionless.
- Friday weekly report can be copied with light editing and does not invent completed work.
- The generated weekly_focus can be traced into the next Monday goal context.

## Repair Loop

After the trial, choose only the top 1-3 issues that most affected actual use.

Use this priority order:

1. P0: Blocks the core loop, corrupts data, generates weekend goals, loses check-ins, ignores feedback, or invents weekly report facts.
2. P1: Makes goals hard to execute, repeatedly oversizes scope, or weakens weekly_focus handoff.
3. P2: Copy, wording, layout, or quality improvements that do not block the MVP loop.

For each repair:

1. Add or update a synthetic eval case under `evals\cases`.
2. Make the smallest code or prompt change that fixes the observed failure.
3. Run the eval suite and backend regressions.
4. Keep unrelated feature ideas in backlog; do not expand MVP scope during repair.

## Validation Commands

Run all evals from Anaconda Prompt:

```bat
cd /d D:\path\to\DayPilot
python -m evals.run_all
```

Run the backend regression scripts from Anaconda Prompt:

```bat
cd /d D:\path\to\DayPilot
python backend\tests\test_today_goal_api.py
python backend\tests\test_checkin_api.py
python backend\tests\test_goal_feedback_api.py
python backend\tests\test_weekly_report_api.py
python backend\tests\test_weekly_focus_handoff.py
```

The trial log template is available at `exports\one_week_trial_log_template.md`.
