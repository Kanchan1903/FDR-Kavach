import logging

from django.core.cache.backends.locmem import LocMemCache
from redis.exceptions import RedisError
from rest_framework.throttling import AnonRateThrottle

logger = logging.getLogger(__name__)
_fallback_cache = LocMemCache(
    "fdr-kavach-login-throttle-fallback",
    {"TIMEOUT": None, "OPTIONS": {"MAX_ENTRIES": 10000}},
)


class LoginRateThrottle(AnonRateThrottle):
    scope = "login"

    def allow_request(self, request, view):
        if getattr(view, "throttle_scope", None) != self.scope:
            return True
        try:
            return super().allow_request(request, view)
        except (RedisError, OSError, TimeoutError):
            # Keep login and MFA available during a Redis outage. The fallback
            # remains rate limited within this API process; account lockout is
            # separately persisted in the database.
            logger.warning("Login throttle cache unavailable; using process-local rate limits.")
            self.cache = _fallback_cache
            return super().allow_request(request, view)
