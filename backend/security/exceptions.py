from rest_framework.views import exception_handler


def safe_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None and response.status_code >= 500:
        response.data = {"detail": "An unexpected server error occurred."}
    return response
