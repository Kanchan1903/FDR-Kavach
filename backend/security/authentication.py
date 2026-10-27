from rest_framework.authentication import SessionAuthentication


class SessionCsrfAuthentication(SessionAuthentication):
    """Cookie sessions require Django's CSRF check for unsafe methods."""

    def authenticate_header(self, request):
        return "Session"
