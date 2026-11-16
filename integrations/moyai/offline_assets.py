"""Remove the pinned upstream HTML's external font requests for local operation."""
import importlib.util
from pathlib import Path
import re


def remove_external_fonts(html):
    return re.sub(r'<link\b[^>]*href=["\x27]https://(?:api|cdn)\.fontshare\.com[^>]*>\s*',
                  '', html, flags=re.IGNORECASE)


if __name__ == '__main__':
    index = Path(importlib.util.find_spec('moyai').origin).parent / 'static/index.html'
    before = index.read_text()
    if before.count('fontshare.com') != 3:
        raise SystemExit('Review the pinned upstream font assets before building this local profile')
    after = remove_external_fonts(before)
    if 'fontshare.com' in after:
        raise SystemExit('External font resources remain')
    index.write_text(after)
