"""Small shared helpers for confirmation-gated Garmin mutations."""

import json
from typing import Any, Dict


def confirmation_required(
    method: str, target: Dict[str, Any], warning: str
) -> str:
    """Return a stable preview response without performing the mutation."""
    return json.dumps(
        {
            "status": "confirmation_required",
            "method": method,
            "target": target,
            "warning": warning,
            "next_step": "Repeat with confirm=true to perform this mutation.",
        },
        indent=2,
    )
