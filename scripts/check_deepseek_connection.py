from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config.settings import load_daypilot_settings  # noqa: E402
from backend.services.llm_client import DeepSeekJsonClient  # noqa: E402


def main() -> None:
    settings = load_daypilot_settings(dotenv_path=ROOT / ".env", prefer_dotenv=True)
    if not settings.deepseek_api_key:
        print("DEEPSEEK_API_KEY is not configured in .env or the environment.")
        raise SystemExit(2)

    messages = [
        {
            "role": "system",
            "content": "Return only JSON. The JSON object must contain key ok with value true.",
        },
        {"role": "user", "content": "Please return {\"ok\": true} as json."},
    ]
    output, metadata = DeepSeekJsonClient(settings).create_json(
        messages,
        task_name="deepseek_connection_check",
        prompt_version="manual_connection_check",
        llm_mode_requested=settings.llm_mode,
    )
    if output.get("ok") is not True:
        print(json.dumps(output, ensure_ascii=False))
        raise SystemExit(1)
    print(f"PASS: DeepSeek JSON connection works with model {metadata.get('model_name')}")


if __name__ == "__main__":
    main()
