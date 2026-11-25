"""Development entry point — validates policy files and prints a project summary.

Run from the repo root:
    python main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent


def main() -> int:
    agents_dir = REPO_ROOT / "agents"
    policies_dir = REPO_ROOT / "policies"
    skills_dir = REPO_ROOT / "skills"

    agents = sorted(d.name for d in agents_dir.iterdir() if d.is_dir())
    policies = sorted(f.name for f in policies_dir.glob("*.yaml"))
    skills = sorted(d.name for d in skills_dir.iterdir() if d.is_dir())

    print("custos-arcae-hermes-agents")
    print(f"  Python  : {sys.version.split()[0]}")
    print(f"  Agents  : {', '.join(agents) or '(none)'}")
    print(f"  Skills  : {', '.join(skills) or '(none)'}")
    print(f"  Policies: {', '.join(policies) or '(none)'}")

    missing = []
    for agent in agents:
        for required in ("system-prompt.md", "policy.yaml"):
            if not (agents_dir / agent / required).exists():
                missing.append(f"agents/{agent}/{required}")

    if missing:
        print("\nMissing required files:")
        for m in missing:
            print(f"  - {m}")
        return 1

    print("\nAll agent directories are complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
