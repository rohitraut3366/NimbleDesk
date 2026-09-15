from __future__ import annotations

import json
import sys
from pathlib import Path

from nimbledesk.creative.davinci import (
    DaVinciWorkerResponse,
    connect_to_resolve,
    execute_in_davinci,
)
from nimbledesk.creative.models import EditPlan


def main() -> None:
    if len(sys.argv) != 8:
        raise SystemExit("DaVinci worker requires seven arguments")
    plan_path = Path(sys.argv[1])
    timeline_path = Path(sys.argv[2])
    output_directory = Path(sys.argv[3])
    render = sys.argv[4] == "1"
    timeout_seconds = float(sys.argv[5])
    result_path = Path(sys.argv[6])
    cancel_path = Path(sys.argv[7])
    try:
        plan = EditPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        result = execute_in_davinci(
            connect_to_resolve(),
            plan,
            timeline_path,
            output_directory,
            render=render,
            timeout_seconds=timeout_seconds,
            cancelled=cancel_path.exists,
        )
        response = DaVinciWorkerResponse(success=True, result=result)
    except Exception as error:
        response = DaVinciWorkerResponse(success=False, error=str(error))
    result_path.write_text(
        json.dumps(response.model_dump(mode="json"), separators=(",", ":")),
        encoding="utf-8",
    )
    raise SystemExit(0 if response.success else 1)


if __name__ == "__main__":
    main()
