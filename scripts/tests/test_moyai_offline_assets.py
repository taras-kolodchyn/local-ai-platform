import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class MoyaiOfflineAssetTests(unittest.TestCase):
    def test_font_links_are_removed_while_local_assets_remain(self):
        path = ROOT / 'integrations/moyai/offline_assets.py'
        self.assertTrue(path.exists(), 'Offline UI font preparation is missing')
        spec = importlib.util.spec_from_file_location('moyai_offline_assets', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        html = '''<head><link rel="preconnect" href="https://api.fontshare.com" />
<link rel="preconnect" href="https://cdn.fontshare.com" crossorigin />
<link rel="stylesheet" href="https://api.fontshare.com/v2/css?f[]=general-sans@1" />
<link rel="stylesheet" href="/assets/local.css" /></head>'''
        result = module.remove_external_fonts(html)
        self.assertNotIn('fontshare.com', result)
        self.assertIn('/assets/local.css', result)
