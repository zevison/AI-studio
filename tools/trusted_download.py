"""HTTPS downloads with Windows/system trust plus the installed public CA bundle."""
import ssl
import urllib.request


def open_public_url(request, *, timeout=35):
    context=ssl.create_default_context()
    # Add public roots without replacing Windows/enterprise trust or disabling TLS.
    try:
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    except ImportError:
        pass
    return urllib.request.urlopen(request, timeout=timeout, context=context)
