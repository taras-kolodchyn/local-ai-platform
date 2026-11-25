"""Request native connection approval before reserving an external write."""
from importlib.util import find_spec
from pathlib import Path


def install(path):
    source = path.read_text()
    hook = '''        if selector is None or read_only_tool(tool_def):
            return args
'''
    replacement = '''        if selector is None:
            # A discovered native tool can be presented as a function even when
            # its provider wrapper requires approval at execution time. Request
            # that approval here, before the external-effect receipt is reserved.
            if not ctx.tool_call_approved and any(
                not connection.shared_github
                and call.tool_name.startswith(connection.prefix + "_")
                and connection.policy.needs_approval(approval_requirement(tool_def))
                for connection in selected_connections(ctx.deps)
            ):
                if not workflow.in_workflow() or workflow.patched("moyai-connection-approval-before-receipt-v1"):
                    raise ApprovalRequired
            return args
        if read_only_tool(tool_def):
            return args
'''
    imports = 'from pydantic_ai.toolsets.abstract import ToolsetTool\n'
    if source.count(hook) != 1 or source.count(imports) != 1:
        raise SystemExit('Review the approval hook in the pinned connection implementation')
    path.write_text(source.replace(imports, imports + 'from temporalio import workflow\n').replace(hook, replacement))


if __name__ == '__main__':
    install(Path(find_spec('moyai.connections').origin))
