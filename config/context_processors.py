from django.conf import settings


def stripe_publishable_key(request):
    """Expose STRIPE_PUBLISHABLE_KEY to templates as `STRIPE_PUBLISHABLE_KEY`.

    This keeps templates simple (they can reference the constant) without
    requiring every view to pass it in the context.
    """
    return {
        'STRIPE_PUBLISHABLE_KEY': getattr(settings, 'STRIPE_PUBLISHABLE_KEY', '')
    }
