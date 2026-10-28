import os

from django.conf import settings
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_wsgi_application()

# Create only missing public demo accounts, after migrations and only in demo mode.
if settings.DEMO_MODE:
    from security.demo_accounts import ensure_demo_accounts

    ensure_demo_accounts()
