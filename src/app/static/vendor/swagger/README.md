# Vendored Swagger UI

The CSP forbids loading scripts or styles from a CDN (`script-src 'self'`), and
FastAPI's built-in `/docs` loads both from jsdelivr. These two files are the
only copies the app serves, and `/docs` is mounted only when
`APP_ENV != production`.

Pinned to swagger-ui-dist@5. To update, re-download from
`https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/` and commit the result.
