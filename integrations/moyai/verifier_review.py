"""Enable confirmed reviews for an explicitly enrolled GitHub connection."""
import os
from importlib.util import find_spec
from pathlib import Path

from moyai.connection_policy import approval_requirement

REVIEW = 'pull_request_review_write'


def allows_tool(scope, grant, policy, tool):
    if tool.name != REVIEW:
        return policy.allows(tool.name)
    verifier = os.environ.get('MOYAI_VERIFIER_CONNECTION_ID')
    return bool(
        verifier and grant.id == verifier and grant.kind == 'github'
        and scope.authority.visibility == 'private' and not grant.read_only
        and policy.product is None and REVIEW in (policy.allowed_tools or ())
        and policy.require_approval and policy.needs_approval(approval_requirement(tool))
    )


if __name__ == '__main__':
    # Change only the scoped discovery filter; upstream owns approval and dispatch.
    path = Path(find_spec('moyai.connections').origin)
    source = path.read_text()
    anchor = 'from .connection_catalog import CatalogToolset, ConnectionCatalog'
    original = 'native.filtered(lambda _ctx, tool: policy.allows(tool.name))'
    if source.count(anchor) != 1 or source.count(original) != 1:
        raise SystemExit('Review the pinned connection filter before building')
    path.write_text(source.replace(anchor, 'from verifier_review import allows_tool\n' + anchor)
                    .replace(original, 'native.filtered(lambda ctx, tool: allows_tool(ctx.deps, grant, policy, tool))'))
