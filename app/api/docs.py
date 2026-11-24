"""The API's interactive documentation at ``/docs``, served so a CDN cannot change it.

FastAPI's own page loads Swagger UI from a CDN at whatever version is newest, with no
check of what arrives. The page is on the app's own origin, where the browser keeps the
PyQxel API key, so a script swapped on the CDN (as happened to polyfill.io) could read
the key. This page pins one version and gives each file's SHA-384 digest, which the
browser checks before running it, so a changed file is refused. Its policy lets scripts
come only from this origin and that CDN, and the page start Swagger UI from a script of
this origin rather than an inline one.

ReDoc's page, which FastAPI also serves, is left out: one set of docs is enough.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

SWAGGER_VERSION = "5.36.0"
"""The Swagger UI release the page loads; the digests below are of its files."""

SWAGGER_CDN = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_VERSION}"

SWAGGER_JS_INTEGRITY = "sha384-fUpRgC19yPykTqzdrUbdAg8NTiu16NEKf9U12J+IJWCg8/1mEI8f2+PEIz60Wyd+"
SWAGGER_CSS_INTEGRITY = "sha384-z/AtSaOHGn5MJ5auAHXUypohywlIWLBZo1jeGmhdpVIbCnFhxAH92cwdV41oyINc"

DOCS_CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'none'",
        f"script-src 'self' {SWAGGER_CDN}/",
        # Swagger UI sets style attributes as it draws.
        f"style-src {SWAGGER_CDN}/ 'unsafe-inline'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ]
)
"""What the docs page may load: Swagger UI's pinned files, and requests to this API."""

DOCS_HEADERS: dict[str, str] = {
    "Content-Security-Policy": DOCS_CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}

_PAGE = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PyQxel API</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="stylesheet" href="{SWAGGER_CDN}/swagger-ui.css"
  integrity="{SWAGGER_CSS_INTEGRITY}" crossorigin="anonymous">
</head>
<body>
<div id="swagger-ui"></div>
<script src="{SWAGGER_CDN}/swagger-ui-bundle.js"
  integrity="{SWAGGER_JS_INTEGRITY}" crossorigin="anonymous"></script>
<script src="/docs/start.js"></script>
</body>
</html>
"""

_START = """SwaggerUIBundle({
  url: '/openapi.json',
  dom_id: '#swagger-ui',
  layout: 'BaseLayout',
  deepLinking: true,
  showExtensions: true,
  showCommonExtensions: true,
  presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
})
"""

router = APIRouter(include_in_schema=False)


@router.get("/docs")
async def docs_page() -> HTMLResponse:
    """The interactive API documentation."""
    return HTMLResponse(_PAGE, headers=DOCS_HEADERS)


@router.get("/docs/start.js")
async def docs_start() -> Response:
    """The script that starts Swagger UI on the docs page."""
    return Response(
        _START,
        media_type="text/javascript",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"},
    )
