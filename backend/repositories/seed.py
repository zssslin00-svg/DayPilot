from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from backend.repositories import daypilot_repository as repo
from backend.repositories.database import PROJECT_ROOT


DEFAULT_SEED_PATH = PROJECT_ROOT / "data" / "seed" / "example_workweek.json"


def load_seed_data(seed_path: str | Path = DEFAULT_SEED_PATH) -> dict[str, Any]:
    return json.loads(Path(seed_path).read_text(encoding="utf-8"))


def seed_example_workweek(
    connection: sqlite3.Connection,
    seed_path: str | Path = DEFAULT_SEED_PATH,
) -> dict[str, int]:
    data = load_seed_data(seed_path)
    counts = {
        "user_profile": 0,
        "daily_goals": 0,
        "goal_versions": 0,
        "daily_checkins": 0,
        "feedback_messages": 0,
        "ability_state": 0,
        "weekly_reports": 0,
        "weekly_focus": 0,
    }

    with connection:
        if data.get("user_profile"):
            repo.create_user_profile(connection, **data["user_profile"])
            counts["user_profile"] += 1

        for daily_goal in data.get("daily_goals", []):
            staged_goal = dict(daily_goal)
            staged_goal.pop("active_version_id", None)
            repo.create_daily_goal(connection, **staged_goal)
            counts["daily_goals"] += 1

        goal_versions = list(data.get("goal_versions", []))
        feedback_link_updates: dict[int, int] = {}

        for version in goal_versions:
            if version.get("feedback_message_id") is None:
                repo.create_goal_version(connection, **version)
                counts["goal_versions"] += 1

        for feedback in data.get("feedback_messages", []):
            staged_feedback = dict(feedback)
            after_version_id = staged_feedback.pop("after_version_id", None)
            feedback_id = repo.create_feedback_message(connection, **staged_feedback)
            counts["feedback_messages"] += 1
            if after_version_id is not None:
                feedback_link_updates[feedback_id] = int(after_version_id)

        for version in goal_versions:
            if version.get("feedback_message_id") is not None:
                repo.create_goal_version(connection, **version)
                counts["goal_versions"] += 1

        for feedback_id, after_version_id in feedback_link_updates.items():
            repo.update_feedback_message(connection, feedback_id, after_version_id=after_version_id)

        for checkin in data.get("daily_checkins", []):
            repo.create_daily_checkin(connection, **checkin)
            counts["daily_checkins"] += 1

        for ability_state in data.get("ability_states", []):
            repo.create_ability_state(connection, **ability_state)
            counts["ability_state"] += 1

        for weekly_report in data.get("weekly_reports", []):
            repo.create_weekly_report(connection, **weekly_report)
            counts["weekly_reports"] += 1

        for weekly_focus in data.get("weekly_focus", []):
            repo.create_weekly_focus(connection, **weekly_focus)
            counts["weekly_focus"] += 1

    return counts
