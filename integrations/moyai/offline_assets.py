"""Prepare the pinned Moyai UI for offline operation and local model profiles."""
import importlib.util
import hashlib
import json
from pathlib import Path
import re
import shutil


def remove_external_fonts(html):
    return re.sub(r'<link\b[^>]*href=["\x27]https://(?:api|cdn)\.fontshare\.com[^>]*>\s*',
                  '', html, flags=re.IGNORECASE)


def describe_analysis_profile(script):
    # The pinned image ships a compiled UI. Keep the description outside Radix's
    # ItemText so it appears in menus without being copied into the selected value.
    # Fail the build if upstream changes this shared SelectItem wrapper.
    start = 'function jy({className:e,children:t,...n}){'
    end = '}const Ry=s9;'
    before, found, tail = script.partition(start)
    wrapper, closed, after = tail.partition(end)
    label = 'p.jsx(_9,{children:t})]})'
    spread = '...n,children:['
    if (script.count(start) != 1 or not found or not closed
            or wrapper.count(label) != 1 or wrapper.count(spread) != 1):
        raise ValueError('Review the pinned upstream SelectItem before building this local profile')
    description = json.dumps('Analysis-only mode of local-qwen. Reads existing files. '
                             'No file changes or shell commands.')
    analysis = 'n.value==="local-qwen-analysis"'
    wrapper = wrapper.replace(spread, '...n,style:' + analysis
                              + '?{...n.style,display:"grid",gap:"0.125rem"}:n.style,'
                              '"aria-description":' + analysis + '?' + description
                              + ':n["aria-description"],children:[')
    wrapper = wrapper.replace(label, 'p.jsx(_9,{children:t}),' + analysis
                              + '&&p.jsx("span",{style:{maxWidth:"min(22rem, calc(100vw - 5rem))",'
                              'whiteSpace:"normal",fontSize:"0.75rem",lineHeight:1.4,opacity:0.75},'
                              'children:' + description + '})]})')
    return before + start + wrapper + end + after


def prepare_ui(index):
    before = index.read_text()
    if before.count('fontshare.com') != 3:
        raise SystemExit('Review the pinned upstream font assets before building this local profile')
    after = remove_external_fonts(before)
    if 'fontshare.com' in after:
        raise SystemExit('External font resources remain')
    entry = re.search(r'src="\./assets/(index-[^"/]+\.js)"', before)
    if entry is None:
        raise SystemExit('Review the pinned upstream UI entry point before building this local profile')
    assets = index.parent / 'assets'
    customized = describe_analysis_profile((assets / entry[1]).read_text())
    # Upstream marks assets immutable for a year. A fresh directory versions the
    # entire module graph, including lazy chunks that import the main bundle.
    namespace = 'local-' + hashlib.sha256(customized.encode()).hexdigest()[:16]
    files = list(assets.iterdir())
    versioned = assets / namespace
    versioned.mkdir()
    for source in files:
        target = versioned / source.name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copyfile(source, target)
    (versioned / entry[1]).write_text(customized)
    index.write_text(after.replace('./assets/', f'./assets/{namespace}/'))


if __name__ == '__main__':
    prepare_ui(Path(importlib.util.find_spec('moyai').origin).parent / 'static/index.html')
