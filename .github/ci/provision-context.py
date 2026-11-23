"""Provision disposable context credentials through the normal startup path."""
import runpy
import secrets


def gateway(method, route, payload=None):
    if (method, route) == ("GET", "/vector_store/list"):
        return {"data": []}
    if (method, route) == ("GET", "/user/info?user_id=local-repository-context"):
        return {"user_role": "internal_user"}
    if (method, route) == ("POST", "/key/generate"):
        return {"key": secrets.token_urlsafe(32)}
    raise ValueError(f"Unexpected gateway request: {method} {route}")


runpy.run_path("scripts/provision-repository-context.py")["provision_context"](gateway)
