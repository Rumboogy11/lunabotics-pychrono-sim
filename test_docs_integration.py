#!/usr/bin/env python3
"""Regression coverage for the documentation set and the GUI Docs tab."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYCHRONO_GUI_NO_REEXEC", "1")

EXPECTED_DOCS = {
    "docs/README.md",
    "docs/quickstart.md",
    "docs/glossary.md",
    "docs/gui-guide.md",
    "docs/wizard-guide.md",
    "docs/cli-reference.md",
    "docs/outputs-and-visualization.md",
    "docs/troubleshooting.md",
    "docs/how-it-works.md",
    "docs/developer-guide.md",
}


class DocEntriesTest(unittest.TestCase):
    def test_all_registration_files_exist(self) -> None:
        import study_gui as gui

        missing = [
            relative for _, relative in gui.DOC_ENTRIES
            if relative is not None and not (ROOT / relative).is_file()
        ]
        self.assertEqual(missing, [], f"missing docs tab files: {missing}")

    def test_new_documentation_pages_are_registered(self) -> None:
        import study_gui as gui

        registered = {relative for _, relative in gui.DOC_ENTRIES if relative is not None}
        self.assertTrue(EXPECTED_DOCS.issubset(registered),
                        f"unregistered docs: {sorted(EXPECTED_DOCS - registered)}")

    def test_gui_info_entries_explain_control_and_cost(self) -> None:
        import study_gui as gui

        for group_name, group in (("PARAM_INFO", gui.PARAM_INFO),
                                  ("SETTINGS_INFO", gui.SETTINGS_INFO)):
            for key, text in group.items():
                with self.subTest(group=group_name, key=key):
                    self.assertNotIn("Think of it as:", text)
                    self.assertIn("Cost:", text)
                    self.assertGreater(len(text), 90)

    def test_gui_info_reflects_verified_behaviour(self) -> None:
        import study_gui as gui

        expectations = {
            "crm_obj": ["full-detail OBJ", "legacy simplified"],
            "load_radius_fraction": ["at least this fraction", "exterior nodes"],
            "raw_stress_every": ["unfiltered raw peak"],
            "particle_fps": ["capped by CRM summary FPS"],
            "tools_orbit_radius": ["five times its radial extent"],
            "surface_repair_order": ["behaves like 'none'"],
            "soil_friction": ["mu_2"],
        }
        for key, phrases in expectations.items():
            with self.subTest(key=key):
                text = gui.PARAM_INFO[key]
                for phrase in phrases:
                    self.assertIn(phrase, text)

        self.assertNotIn("coarse OBJ", gui.PARAM_INFO["crm_obj"])

    def test_docs_hub_links_every_guide(self) -> None:
        index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        missing = [
            str(Path(path).name) for path in EXPECTED_DOCS
            if Path(path).name != "README.md" and Path(path).name not in index
        ]
        self.assertEqual(missing, [], f"docs hub does not link: {missing}")


class DocsTabTest(unittest.TestCase):
    def setUp(self) -> None:
        import tkinter as tk

        import study_gui as gui

        self.tk = tk
        self.gui = gui
        self._tmp = tempfile.TemporaryDirectory(prefix="gui_docs_", dir="/tmp/opencode")
        self.addCleanup(self._tmp.cleanup)
        self.patches = [
            patch.object(gui, "DEFAULT_FILE", Path(self._tmp.name) / "defaults.json"),
            patch.object(gui, "SETTINGS_FILE", Path(self._tmp.name) / "settings.json"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.app = gui.StudyGUI(self.root)

    def _render_markdown(self, body: str) -> str:
        self.app.doc_text.configure(state="normal")
        self.app.doc_text.delete("1.0", "end")
        self.app._render_doc_markdown(body)
        rendered = self.app.doc_text.get("1.0", "end")
        self.app.doc_text.configure(state="disabled")
        return rendered

    def test_tree_lists_every_registered_page(self) -> None:
        items = self.app.docs_tree.get_children()
        self.assertEqual(len(items), len(self.gui.DOC_ENTRIES))
        labels = [self.app.docs_tree.item(iid, "text") for iid in items]
        self.assertIn("Quick start (beginners)", labels)
        self.assertIn("GUI guide", labels)
        self.assertIn("Developer guide", labels)

    def test_selecting_each_page_renders_content(self) -> None:
        for title, relative in self.gui.DOC_ENTRIES:
            with self.subTest(title=title):
                index = [name for name, _ in self.gui.DOC_ENTRIES].index(title)
                self.app.docs_tree.selection_set(f"doc_{index}")
                self.app._show_doc()
                rendered = self.app.doc_text.get("1.0", "end").strip()
                self.assertTrue(rendered, f"{title} rendered empty")
                if relative is None:
                    self.assertIn("GUI parameter reference", rendered)
                else:
                    expected = (ROOT / relative).read_text(encoding="utf-8").splitlines()[0]
                    expected = expected.lstrip("# ").strip()
                    self.assertIn(expected, rendered)

    def test_markdown_tables_use_real_tree_widgets(self) -> None:
        self._render_markdown(
            "| Command | Purpose |\n"
            "| --- | --- |\n"
            "| run | simulate |\n"
        )
        self.assertEqual(len(self.app._doc_tables), 1)
        tree = self.app._doc_tables[0]
        self.assertEqual(tree.heading("col0")["text"], "Command")
        self.assertEqual(tree.heading("col1")["text"], "Purpose")
        rows = [tree.item(item, "values") for item in tree.get_children()]
        self.assertEqual(rows, [("run", "simulate")])
        self.assertGreater(tree["height"], 0)

    def test_table_cells_keep_link_targets_and_clear_on_rerender(self) -> None:
        self._render_markdown(
            "| Goal | Page |\n"
            "| --- | --- |\n"
            "| Learn terms | [Glossary](glossary.md) |\n"
            "| Get started | [Quick start](quickstart.md) and [GUI](gui-guide.md) |\n"
        )
        tree = self.app._doc_tables[0]
        targets = list(tree._doc_table_links.values())
        self.assertEqual(targets[0], "glossary.md")
        second_row = tree.get_children()[1]
        spans = tree._doc_table_cell_links[(second_row, "#2")][1]
        self.assertEqual([span[2] for span in spans], ["quickstart.md", "gui-guide.md"])

        self._render_markdown("| A | B |\n| --- | --- |\n| 1 | 2 |\n")
        self.assertEqual(len(self.app._doc_tables), 1)
        self.assertFalse(self.app._doc_tables[0]._doc_table_links)

    def test_headings_and_inline_markup_are_styled(self) -> None:
        rendered = self._render_markdown(
            "# Title\n\n"
            "## Section\n\n"
            "### Subsection\n\n"
            "**Bold**, *italic*, `code`, [Glossary](glossary.md).\n"
        )
        for text in ("Title", "Section", "Subsection", "Bold", "italic", "code", "Glossary"):
            with self.subTest(text=text):
                self.assertIn(text, rendered)
        self.assertNotIn("**", rendered)
        self.assertNotIn("`", rendered)
        for tag in ("doc_title", "doc_heading", "doc_subheading",
                    "doc_bold", "doc_italic", "doc_inline_code"):
            with self.subTest(tag=tag):
                self.assertTrue(self.app.doc_text.tag_ranges(tag))
        self.assertTrue(
            any(name.startswith("doc_link_") for name in self.app.doc_text.tag_names())
        )

    def test_fenced_code_uses_shaded_box_without_fences(self) -> None:
        rendered = self._render_markdown(
            "Before\n\n```bash\n./run_study.sh --help\n```\n\nAfter\n"
        )
        self.assertNotIn("```", rendered)
        self.assertIn("./run_study.sh --help", rendered)
        self.assertIn("Before", rendered)
        self.assertIn("After", rendered)
        self.assertTrue(self.app.doc_text.tag_ranges("doc_code_box"))

    def test_heading_anchor_scrolls_to_rendered_heading(self) -> None:
        self._render_markdown("# Setup Guide\n\n## Install locally\n\nBody.\n")
        self.assertTrue(self.app._doc_scroll_to_heading("install-locally"))
        self.assertIn("Docs: jumped to 'Install locally'", self.app.status_var.get())

    def test_local_link_switches_docs_page(self) -> None:
        self.app.docs_tree.selection_set("doc_0")
        self.app._show_doc()
        self.app._doc_follow_link("glossary.md")
        selection = self.app.docs_tree.selection()
        self.assertEqual(len(selection), 1)
        values = self.app.docs_tree.item(selection[0], "values")
        self.assertEqual(values[0], "docs/glossary.md")

    def test_external_link_opens_browser(self) -> None:
        with patch("webbrowser.open") as opened:
            self.app._doc_follow_link("https://example.com/docs")
        opened.assert_called_once_with("https://example.com/docs")

    def test_find_highlights_match_on_current_page(self) -> None:
        self.app.docs_tree.selection_set("doc_0")
        self.app._show_doc()
        self.app.doc_search_var.set("Glossary")
        self.app._find_doc_text()
        self.assertTrue(self.app.doc_text.tag_ranges("doc_search"))
        self.assertIn("Docs: found", self.app.status_var.get())


if __name__ == "__main__":
    unittest.main()
