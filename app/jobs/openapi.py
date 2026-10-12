"""Write the API's OpenAPI schema, from which the web frontend's types are generated::

    python -m app.jobs.openapi            # writes web/openapi.json
    cd web && npm run gen:api             # regenerates src/api/schema.d.ts from it

The tests fail when ``web/openapi.json`` no longer matches the API.
"""

import argparse
import json
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT

SCHEMA_PATH: Path = PROJECT_ROOT / "web" / "openapi.json"


def openapi_schema() -> dict[str, Any]:
    """Return the API's OpenAPI schema."""
    from app.main import app

    return app.openapi()


def render(schema: dict[str, Any]) -> str:
    """Format ``schema`` as the committed file is formatted."""
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Write the API's OpenAPI schema.")
    parser.add_argument("path", nargs="?", type=Path, default=SCHEMA_PATH)
    arguments = parser.parse_args()
    arguments.path.write_text(render(openapi_schema()), encoding="utf-8")
    print(f"Wrote {arguments.path}.")


if __name__ == "__main__":
    main()
