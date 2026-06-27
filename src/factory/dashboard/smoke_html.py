"""Fallback smoke HTML when SPA dist is absent (dev / pre-build)."""


def smoke_index() -> str:
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8" />
<title>Factory Dashboard</title></head>
<body><p>SPA not built — run <code>bun run build:dashboard</code>.</p></body></html>"""