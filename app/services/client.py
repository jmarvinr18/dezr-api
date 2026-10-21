from flask import request


def client_ip() -> str | None:
    """The caller's address. Behind proxies this is the visitor's real IP only when
    TRUSTED_PROXY_COUNT matches them (see the ProxyFix in create_app)."""
    return request.remote_addr
