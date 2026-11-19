import importlib.util
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]


class MoyaiOfflineAssetTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / 'integrations/moyai/offline_assets.py'
        self.assertTrue(path.exists(), 'Offline UI font preparation is missing')
        spec = importlib.util.spec_from_file_location('moyai_offline_assets', path)
        self.assets = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.assets)

    def test_font_links_are_removed_while_local_assets_remain(self):
        html = '''<head><link rel="preconnect" href="https://api.fontshare.com" />
<link rel="preconnect" href="https://cdn.fontshare.com" crossorigin />
<link rel="stylesheet" href="https://api.fontshare.com/v2/css?f[]=general-sans@1" />
<link rel="stylesheet" href="/assets/local.css" /></head>'''
        result = self.assets.remove_external_fonts(html)
        self.assertNotIn('fontshare.com', result)
        self.assertIn('/assets/local.css', result)

    def test_description_is_outside_item_text_so_selected_model_stays_compact(self):
        # The shared SelectItem is used by both the home and chat model pickers.
        wrapper = ('function jy({className:e,children:t,...n}){return p.jsxs(x9,{'
                   '"data-slot":"select-item",...n,children:['
                   'p.jsx("span",{children:"check"}),p.jsx(_9,{children:t})]})}const Ry=s9;')
        result = self.assets.describe_analysis_profile('before;' + wrapper + 'after;')
        self.assertTrue(result.startswith('before;'))
        self.assertTrue(result.endswith('after;'))
        self.assertIn('p.jsx(_9,{children:t}),', result)
        self.assertIn('n.value==="local-qwen-analysis"&&p.jsx("span",', result)
        self.assertIn('Analysis-only mode of local-qwen.', result)
        self.assertIn('No file changes or shell commands.', result)
        self.assertIn('"aria-description":', result)
        with self.assertRaisesRegex(ValueError, 'SelectItem'):
            self.assets.describe_analysis_profile(result)

    def test_upstream_selector_drift_and_duplicate_patching_fail_the_build(self):
        for bundle in ('unrecognized upstream bundle',
                       'function jy({className:e,children:t,...n}){changed}const Ry=s9;'):
            with self.subTest(bundle=bundle), self.assertRaisesRegex(ValueError, 'SelectItem'):
                self.assets.describe_analysis_profile(bundle)

    def test_changed_ui_uses_fresh_asset_urls_including_lazy_imports(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / 'assets'
            assets.mkdir()
            wrapper = ('function jy({className:e,children:t,...n}){return p.jsxs(x9,{'
                       '"data-slot":"select-item",...n,children:['
                       'p.jsx("span",{}),p.jsx(_9,{children:t})]})}const Ry=s9;')
            (assets / 'index-old.js').write_text(wrapper)
            (assets / 'lazy.js').write_text('import "./index-old.js";')
            (assets / 'local.css').write_text('body {}')
            index = root / 'index.html'
            index.write_text('<head>' + '<link href="https://api.fontshare.com" />' * 3
                             + '<script src="./assets/index-old.js"></script>'
                             + '<link href="./assets/local.css" /></head>')
            self.assets.prepare_ui(index)
            html = index.read_text()
            entry = re.search(r'src="\./(assets/[^"/]+/index-old.js)"', html)
            self.assertIsNotNone(entry, 'Immutable asset URLs must change after customization')
            versioned = (root / entry[1]).parent
            self.assertIn('Analysis-only mode of local-qwen.', (versioned / 'index-old.js').read_text())
            self.assertEqual((versioned / 'lazy.js').read_text(), 'import "./index-old.js";')
            self.assertIn(f'./assets/{versioned.name}/local.css', html)
            self.assertEqual((assets / 'index-old.js').read_text(), wrapper)
            self.assertNotIn('fontshare.com', html)
