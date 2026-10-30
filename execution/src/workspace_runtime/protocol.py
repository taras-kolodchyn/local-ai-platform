"""Public profile card contract shared by provisioning and runtime."""

def card(profile):
    return {'protocolVersion': '0.3.0', 'name': 'Workspace ' + profile,
        'description': 'Local source work with private persistent memory',
        'url': f'http://workspace-{profile}:8100/profiles/{profile}', 'version': '0.1.0',
        'preferredTransport': 'JSONRPC', 'capabilities': {'streaming': False, 'pushNotifications': False},
        'defaultInputModes': ['text/plain'], 'defaultOutputModes': ['text/plain'],
        'skills': [{'id': profile, 'name': profile.title(),
            'description': 'Source analysis and private memory', 'tags': ['workspace', profile]}]}
