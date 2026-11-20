#!/usr/bin/env python3
"""Check fresh dashboard sessions using environment or standard-input credentials."""

import argparse
from http.client import HTTPException
from http.cookiejar import CookieJar
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)


class DashboardLoginError(Exception):
    """A safe diagnostic that never includes response bodies or credentials."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def session():
    cookies = CookieJar()
    # Local credentials must not be sent through an environment-configured proxy.
    opener = build_opener(ProxyHandler({}), NoRedirects(), HTTPCookieProcessor(cookies))
    return opener, cookies


def open_response(opener, request, service):
    try:
        return opener.open(request, timeout=10)
    except HTTPError as response:
        # Inspect status/headers only; even failure bodies may contain secrets.
        return response
    except (URLError, OSError, HTTPException):
        raise DashboardLoginError(f"{service} dashboard is unreachable or timed out") from None


def has_cookie(cookies, name):
    return any(cookie.name == name and cookie.value for cookie in cookies)


def check_litellm(base, username, password):
    opener, cookies = session()
    request = Request(
        base + '/login',
        data=urlencode({'username': username, 'password': password}).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    with open_response(opener, request, 'LiteLLM') as response:
        if response.code not in (302, 303):
            raise DashboardLoginError(f"LiteLLM dashboard login failed (HTTP {response.code})")
        destination = urlsplit(urljoin(base + '/', response.headers.get('Location', '')))
        origin = urlsplit(base)
        if ((destination.scheme, destination.netloc) != (origin.scheme, origin.netloc)
                or not (destination.path == '/ui' or destination.path.startswith('/ui/'))
                or parse_qs(destination.query).get('login') != ['success']
                or not has_cookie(cookies, 'token')):
            raise DashboardLoginError('LiteLLM dashboard login did not create a successful UI session')
    # Do not follow or print the redirect, which may contain authentication data.


def check_grafana(base, password):
    opener, cookies = session()
    request = Request(
        base + '/login',
        data=json.dumps({'user': 'admin', 'password': password}).encode(),
        headers={'Content-Type': 'application/json'},
    )
    with open_response(opener, request, 'Grafana') as response:
        if response.code != 200:
            raise DashboardLoginError(f"Grafana dashboard login failed (HTTP {response.code})")
        if not has_cookie(cookies, 'grafana_session'):
            raise DashboardLoginError('Grafana dashboard login did not create a fresh session')

    with open_response(opener, Request(base + '/api/user'), 'Grafana') as response:
        if response.code != 200:
            raise DashboardLoginError(f"Grafana session verification failed (HTTP {response.code})")
        body = response.read(65537)
        if len(body) > 65536:
            raise DashboardLoginError('Grafana session verification returned an oversized response')
        try:
            identity = json.loads(body)
        except (ValueError, UnicodeError):
            raise DashboardLoginError('Grafana session verification returned invalid JSON') from None
        if (not isinstance(identity, dict) or identity.get('login') != 'admin'
                or identity.get('isGrafanaAdmin') is not True):
            raise DashboardLoginError('Grafana session does not belong to the configured server admin')


def read_litellm_password():
    try:
        password = sys.stdin.buffer.read().decode('utf-8') if sys.stdin is not None else ''
    except (OSError, ValueError):
        raise DashboardLoginError('Could not read the LiteLLM UI password as UTF-8 from stdin') from None
    if not password or password == 'GENERATE_ME' or '\0' in password:
        raise DashboardLoginError('Invalid LiteLLM UI password on stdin')
    return password


def main(litellm_base='http://127.0.0.1:4000', grafana_base='http://127.0.0.1:3000', *, argv=()):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--litellm-password-stdin', action='store_true',
                        help='Read the exact UTF-8 LiteLLM UI password from standard input')
    options = parser.parse_args(argv)
    try:
        username = os.environ.get('UI_USERNAME') or 'admin'
        if options.litellm_password_stdin:
            password = read_litellm_password()
        else:
            password = os.environ.get('UI_PASSWORD') or os.environ.get('LITELLM_MASTER_KEY', '')
            if not password or password == 'GENERATE_ME':
                raise DashboardLoginError('Set UI_PASSWORD or LITELLM_MASTER_KEY in .env before testing login')
        grafana_password = os.environ.get('GRAFANA_ADMIN_PASSWORD', '')
        if not grafana_password or grafana_password == 'GENERATE_ME':
            raise DashboardLoginError('Set GRAFANA_ADMIN_PASSWORD in .env before testing login')
        check_litellm(litellm_base, username, password)
        print('✓ LiteLLM dashboard login passed')
        check_grafana(grafana_base, grafana_password)
        print('✓ Grafana dashboard login passed')
    except DashboardLoginError as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    except (OSError, HTTPException, ValueError):
        # Do not emit exception text or a traceback with authentication data.
        print('ERROR: Dashboard login check could not complete', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main(argv=sys.argv[1:]))
