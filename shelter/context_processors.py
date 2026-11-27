from django.conf import settings


def csp_nonce(request):
    """The per-request nonce for inline <script> tags: <script nonce="{{ csp_nonce }}">."""
    return {"csp_nonce": getattr(request, "csp_nonce", "")}


def google_api_key(request):
    """The browser Google Maps key of this server: {{ GOOGLE_API_KEY }}."""
    return {"GOOGLE_API_KEY": settings.GOOGLE_API_KEY}


def site_environment(request):
    """local, uat or prod, for the browser-tab icon: {{ SITE_ENVIRONMENT }}."""
    return {"SITE_ENVIRONMENT": settings.SITE_ENVIRONMENT}
