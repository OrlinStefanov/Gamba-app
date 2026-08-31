"""Checks on the static web app under docs/.

These are the failures that are invisible until someone opens the page on a
phone with no signal: a file listed for offline caching that does not exist, a
module importing something the bundle does not ship, a manifest that stops the
app installing.
"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DOCS = Path(__file__).resolve().parents[1] / "docs"


class TestServiceWorker(unittest.TestCase):
    def setUp(self):
        self.source = (DOCS / "sw.js").read_text(encoding="utf-8")
        listed = re.search(r"const SHELL = \[(.*?)\];", self.source, re.S)
        self.assertIsNotNone(listed, "could not find the SHELL list")
        self.shell = re.findall(r"'\./([^']*)'", listed.group(1))

    def test_every_cached_file_exists(self):
        # A typo here does not fail loudly - it just means the app never works
        # offline, and only on a phone with no signal.
        for entry in self.shell:
            if not entry:
                continue  # './' is the directory itself
            with self.subTest(entry=entry):
                self.assertTrue((DOCS / entry).is_file(), f"docs/{entry} is listed but missing")

    def test_every_module_is_cached(self):
        modules = {f"js/{p.name}" for p in (DOCS / "js").glob("*.js")}
        self.assertEqual(modules - set(self.shell), set(), "modules missing from the offline shell")

    def test_forecast_data_is_never_cached(self):
        # A stale beach flag is worse than no beach flag.
        self.assertIn("url.origin !== self.location.origin", self.source)


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((DOCS / "manifest.webmanifest").read_text(encoding="utf-8"))

    def test_installable(self):
        self.assertEqual(self.manifest["display"], "standalone")
        self.assertTrue(self.manifest["name"])
        self.assertTrue(self.manifest["short_name"])
        self.assertEqual(self.manifest["start_url"], ".")

    def test_icons_exist_and_cover_the_required_sizes(self):
        sizes = set()
        for icon in self.manifest["icons"]:
            path = DOCS / icon["src"]
            self.assertTrue(path.is_file(), icon["src"])
            self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n", icon["src"])
            sizes.add(icon["sizes"])
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)

    def test_a_maskable_icon_is_offered(self):
        purposes = {icon.get("purpose") for icon in self.manifest["icons"]}
        self.assertIn("maskable", purposes)


class TestPageWiring(unittest.TestCase):
    def setUp(self):
        self.html = (DOCS / "index.html").read_text(encoding="utf-8")

    def test_references_resolve(self):
        for href in re.findall(r'(?:href|src)="([^":]+)"', self.html):
            with self.subTest(href=href):
                self.assertTrue((DOCS / href).is_file(), href)

    def test_loads_as_a_module(self):
        self.assertIn('<script type="module" src="js/app.js"></script>', self.html)

    def test_every_element_the_script_reaches_for_exists(self):
        app = (DOCS / "js" / "app.js").read_text(encoding="utf-8")
        static_ids = set(re.findall(r"\$\('([a-zA-Z]+)'\)", app))
        # Ids the script creates itself, rather than finding in index.html.
        created = set(re.findall(r'id="([a-zA-Z]+)"', app))
        for element_id in static_ids - created:
            with self.subTest(id=element_id):
                self.assertIn(f'id="{element_id}"', self.html, f"#{element_id} is not in index.html")


class TestJsImports(unittest.TestCase):
    def test_relative_imports_all_resolve(self):
        for module in (DOCS / "js").glob("*.js"):
            source = module.read_text(encoding="utf-8")
            for target in re.findall(r"from '(\./[^']+)'", source):
                with self.subTest(module=module.name, target=target):
                    self.assertTrue((module.parent / target).is_file(), f"{module.name} -> {target}")


if __name__ == "__main__":
    unittest.main()
