"""Export the current app plus a dependency-pruned, five-tool-only document."""

import json
from copy import deepcopy
from pathlib import Path


def restricted(document):
    output = deepcopy(document)
    output["info"]["title"] = "运筹助手五工具（契约 1.0）"
    output["paths"] = {
        p: v for p, v in output["paths"].items() if p.startswith("/api/v1/agent-tools/")
    }
    output["components"]["securitySchemes"].pop("BrowserLogin", None)
    schemas = output["components"]["schemas"]
    found = set()

    def visit(value):
        if isinstance(value, dict):
            ref = value.get("$ref", "")
            prefix = "#/components/schemas/"
            if ref.startswith(prefix):
                name = ref.removeprefix(prefix)
                if name not in found:
                    found.add(name)
                    visit(schemas[name])
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(output["paths"])
    output["components"]["schemas"] = {name: schemas[name] for name in sorted(found)}
    return output


def main():
    from app.main import create_app

    document = create_app().openapi()
    root = Path(__file__).resolve().parents[1]
    for filename, content in [
        ("openapi.json", document),
        ("agent-tools.openapi.json", restricted(document)),
    ]:
        (root / filename).write_text(
            json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
