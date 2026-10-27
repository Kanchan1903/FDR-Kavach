class SecurityHeadersMiddleware:
    """Apply restrictive browser policies to JSON/API responses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/"):
            response.setdefault(
                "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            )
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.setdefault("Cache-Control", "no-store")
        return response
