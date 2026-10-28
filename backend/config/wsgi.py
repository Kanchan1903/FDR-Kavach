import os

from django.conf import settings
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_wsgi_application()

# The public demo uses seeded users and sample records. Initialize them once
# per API process after Django is ready, but never seed outside DEMO_MODE.
if settings.DEMO_MODE:
    call_command("seed_demo", verbosity=0)
