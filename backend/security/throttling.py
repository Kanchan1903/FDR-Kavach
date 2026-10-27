from rest_framework.throttling import AnonRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    scope = "login"

    def allow_request(self, request, view):
        if getattr(view, "throttle_scope", None) != self.scope:
            return True
        return super().allow_request(request, view)
