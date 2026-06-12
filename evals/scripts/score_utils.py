from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


EVALS_ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = EVALS_ROOT / "cases"
RESULTS_DIR = EVALS_ROOT / "results"


DELIVERABLE_WORDS = [
    "接口",
    "测试",
    "页面",
    "文档",
    "记录",
    "代码",
    "脚本",
    "schema",
    "API",
    "交付",
    "改动",
    "切片",
    "结果",
]
VAGUE_WORDS = ["继续学习", "继续研究", "持续推进", "优化完善", "看看", "研究一下"]
MULTI_GOAL_MARKERS = ["；", "\n-", "\n1", "同时", "并且", "以及"]
WEEKDAY_WORDS = ["周一", "周二", "周三", "周四", "周五", "星期一", "星期二", "星期三", "星期四", "星期五"]
OUTCOME_WORDS = [
    "完成",
    "交付",
    "跑通",
    "形成",
    "验证",
    "补齐",
    "收敛",
    "产出",
    "记录",
    "生成",
    "汇总",
    "运行",
    "启动",
    "通过",
    "写出",
    "补充",
    "结果",
    "闭环",
    "测试",
    "报告",
    "笔记",
    "数据集",
    "指标",
]


def load_cases(name: str) -> list[dict[str, Any]]:
    return json.loads((CASES_DIR / name).read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_result(suite: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    passed = [item for item in results if item["passed"]]
    average = round(sum(item["score"] for item in results) / max(1, len(results)), 2)
    summary = {
        "suite": suite,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(results),
        "passed": len(passed),
        "failed": len(results) - len(passed),
        "average_score": average,
        "results": results,
    }
    write_json(RESULTS_DIR / f"{suite}_results.json", summary)
    return summary


def case_result(
    case_id: str,
    suite: str,
    score: int,
    hard_failures: list[str],
    evidence: list[str],
    suggested_fix: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "suite": suite,
        "score": max(0, min(100, score)),
        "passed": score >= 80 and not hard_failures,
        "hard_failures": hard_failures,
        "evidence": evidence,
        "suggested_fix": suggested_fix,
        **(extra or {}),
    }


def has_deliverable(text: str) -> bool:
    return any(word.lower() in str(text).lower() for word in DELIVERABLE_WORDS)


def has_vague_text(text: str) -> bool:
    return any(word in str(text) for word in VAGUE_WORDS)


def has_multi_goal_marker(text: str) -> bool:
    return any(marker in str(text) for marker in MULTI_GOAL_MARKERS)


def has_weekday_log(text: str) -> bool:
    return any(word in str(text) for word in WEEKDAY_WORDS)


def is_outcome_text(text: str) -> bool:
    return any(word in str(text) for word in OUTCOME_WORDS)


def render_markdown_summary(summaries: list[dict[str, Any]]) -> str:
    lines = [
        "# DayPilot Regression Summary",
        "",
        f"Run date: {datetime.now().date().isoformat()}",
        "",
        "## Overall",
    ]
    for summary in summaries:
        lines.append(
            f"- {summary['suite']}: average {summary['average_score']}, "
            f"pass {summary['passed']}/{summary['total']}, hard fails "
            f"{sum(1 for item in summary['results'] if item['hard_failures'])}"
        )
    lines.extend(
        [
            "",
            "## New Failures",
            "| Case | Suite | Score | Hard fail | Evidence | Suggested fix |",
            "|---|---|---:|---|---|---|",
        ]
    )
    failures = [item for summary in summaries for item in summary["results"] if not item["passed"]]
    if not failures:
        lines.append("| - | - | - | - | - | - |")
    for item in failures:
        lines.append(
            "| {id} | {suite} | {score} | {hard} | {evidence} | {fix} |".format(
                id=item["id"],
                suite=item["suite"],
                score=item["score"],
                hard="<br>".join(item["hard_failures"]) or "-",
                evidence="<br>".join(item["evidence"][:3]) or "-",
                fix=item["suggested_fix"] or "-",
            )
        )
    return "\n".join(lines) + "\n"
