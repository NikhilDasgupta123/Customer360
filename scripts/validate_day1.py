"""Small validation script for Day 1 backend planning data."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from customergraph.core.rbac import can_access, validate_rbac_matrix  # noqa: E402


def main() -> None:
    result = validate_rbac_matrix()
    assert result["ok"], result["errors"]
    assert can_access("admin", "admin_audit") is True
    assert can_access("support_agent", "support") is True
    assert can_access("support_agent", "admin_audit") is False
    assert can_access("manager", "dashboard") is True
    print("✅ Day 1 backend structure validation passed")


if __name__ == "__main__":
    main()