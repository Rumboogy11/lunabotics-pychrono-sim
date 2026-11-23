#!/usr/bin/env python3
"""Coverage for the optional modern-styled GUI front end."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Match the existing GUI tests: keep the import from re-exec'ing into system
# Python when the suite runs under chrono-env.
os.environ.setdefault("PYCHRONO_GUI_NO_REEXEC", "1")

import study_gui as gui  # noqa: E402
import study_gui_modern as modern  # noqa: E402


class ModernGuiTest(unittest.TestCase):
    def setUp(self) -> None:
        import tkinter as tk

        self._tmp = tempfile.TemporaryDirectory(prefix="modern_gui_", dir="/tmp/opencode")
        self._original_defaults = gui.DEFAULT_FILE
        self._original_settings = gui.SETTINGS_FILE
        gui.DEFAULT_FILE = Path(self._tmp.name) / "defaults.json"
        gui.SETTINGS_FILE = Path(self._tmp.name) / "settings.json"
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = modern.ModernStudyGUI(self.root)

    def tearDown(self) -> None:
        self.root.destroy()
        gui.DEFAULT_FILE = self._original_defaults
        gui.SETTINGS_FILE = self._original_settings
        self._tmp.cleanup()

    def _buttons(self):
        from tkinter import ttk

        return [w for w in self.app._walk_widgets() if isinstance(w, ttk.Button)]

    def test_modern_gui_is_built_on_the_classic_gui(self) -> None:
        self.assertTrue(issubclass(modern.ModernStudyGUI, gui.StudyGUI))
        tabs = [self.app.notebook.tab(tab, "text") for tab in self.app.notebook.tabs()]
        self.assertEqual(tabs, [
            "Study Wizard",
            "Output Preview",
            "Tools / View Results",
            "Docs",
            "Defaults",
            "Settings",
        ])

    def test_header_is_packed_above_the_existing_notebook(self) -> None:
        header = self.app._modern_header
        self.assertIsNotNone(header)
        self.assertEqual(header.winfo_parent(), self.app.notebook.winfo_parent())
        self.assertEqual(header.winfo_manager(), "pack")
        packed = self.root.pack_slaves()
        self.assertLess(packed.index(header), packed.index(self.app.notebook))

    def test_step_chips_navigate_and_highlight(self) -> None:
        from tkinter import ttk

        for key in ("configure", "simulate", "review"):
            chip = self.app._step_chips[key]
            self.assertIsInstance(chip, ttk.Button)
            self.assertTrue(chip.cget("command"))

        self.app._step_chips["simulate"].invoke()
        self.assertEqual(str(self.app.notebook.select()), str(self.app.study_tab))
        self.assertEqual(self.app.step_nb.index(self.app.step_nb.select()), 5)
        self.assertEqual(str(self.app._step_chips["simulate"].cget("style")),
                         "Modern.Chip.Active.TButton")

        self.app._step_chips["review"].invoke()
        self.assertEqual(str(self.app.notebook.select()), str(self.app.tools_tab))
        self.assertEqual(str(self.app._step_chips["review"].cget("style")),
                         "Modern.Chip.Active.TButton")
        self.assertEqual(str(self.app._step_chips["simulate"].cget("style")),
                         "Modern.Chip.TButton")

    def test_main_actions_get_accent_button_style(self) -> None:
        styles = {str(w.cget("text")): str(w.cget("style")) for w in self._buttons()}
        self.assertEqual(styles.get("Run study"), "Modern.Primary.TButton")
        self.assertEqual(styles.get("Stop running process"), "Modern.Danger.TButton")
        self.assertEqual(styles.get("Info"), "Modern.Ghost.TButton")
        self.assertEqual(self.app.modern_palette["accent"], modern.LIGHT_PALETTE["accent"])

    def test_button_families_have_distinct_colours(self) -> None:
        styles = {str(w.cget("text")): str(w.cget("style")) for w in self._buttons()}
        expected = {
            "Run study": "Modern.Primary.TButton",
            "Run one-way FEA only (existing CRM)": "Modern.Teal.TButton",
            "Save config/commands (dry run)": "Modern.Soft.TButton",
            "Load study_config.json": "Modern.Info.TButton",
            "Open interactive result plots": "Modern.Success.TButton",
            "Stop running process": "Modern.Danger.TButton",
            "Skip current step": "Modern.Warning.TButton",
            "Info": "Modern.Ghost.TButton",
        }
        for text, style in expected.items():
            self.assertEqual(styles.get(text), style, text)

        colours = [value[0] for value in modern._button_colors(self.app.modern_palette).values()]
        self.assertGreaterEqual(len(set(colours)), 7)

    def test_rounded_ttk_elements_are_installed(self) -> None:
        from tkinter import ttk

        if modern.Image is None:
            self.skipTest("Pillow is not installed; square ttk fallback is expected")
        style = ttk.Style()
        self.assertEqual(style.layout("Modern.Info.TButton")[0][0],
                         self.app._rounded_name("Modern.Info.TButton"))
        self.assertEqual(style.layout("TEntry")[0][0],
                         self.app._rounded_key("Entry.field"))
        self.assertEqual(style.layout("TNotebook.Tab")[0][0],
                         self.app._rounded_key("Notebook.tab"))
        self.assertGreater(len(self.app._rounded_images), 20)

    def test_tab_padding_is_identical_for_every_state(self) -> None:
        from tkinter import ttk

        style = ttk.Style()
        expected = tuple(self.app._px(value) for value in (8, 4))
        for tab_style in ("TNotebook.Tab", "Modern.TNotebook.Tab"):
            self.assertEqual(style.map(tab_style).get("padding", []), [])
            self.assertEqual(
                modern._padding_pair(style.lookup(tab_style, "padding")), expected
            )

    def test_button_padding_is_compact_and_scales(self) -> None:
        from tkinter import ttk

        style = ttk.Style()
        expected = tuple(self.app._px(value) for value in (6, 4))
        self.assertEqual(
            modern._padding_pair(style.lookup("TButton", "padding")), expected
        )
        self.app.settings = dict(self.app.settings, ui_scale=1.5)
        self.app._apply_modern_theme(self.app.settings)
        self.assertEqual(
            modern._padding_pair(style.lookup("TButton", "padding")), (9, 6)
        )

    def test_custom_button_font_is_retained_and_small(self) -> None:
        from tkinter import font as tkfont

        names = self.root.tk.call("font", "names")
        self.assertIn(modern.BUTTON_FONT, names)
        font = tkfont.nametofont(modern.BUTTON_FONT, root=self.root)
        self.assertLessEqual(font.cget("size"), 8)
        self.assertEqual(font.cget("weight"), "bold")

    def test_parameter_fields_have_compact_padding(self) -> None:
        from tkinter import ttk

        style = ttk.Style()
        expected = tuple(self.app._px(value) for value in (6, 3))
        for name in ("TEntry", "TCombobox", "TSpinbox"):
            self.assertEqual(
                modern._padding_pair(style.lookup(name, "padding")), expected, name
            )

    def test_gui_scale_affects_bars_and_buttons(self) -> None:
        from tkinter import ttk

        nav = next(
            widget for widget in self.app._walk_widgets()
            if widget.winfo_manager() == "pack"
            and {"< Back", "Next >"} <= {
                str(child.cget("text")) for child in widget.winfo_children()
                if isinstance(child, ttk.Button)
            }
        )
        initial = modern._padding_pair(nav.pack_info()["padx"])
        self.assertEqual(initial, (8, 8))
        self.app.settings = dict(self.app.settings, ui_scale=1.5)
        self.app._apply_modern_theme(self.app.settings)
        self.assertEqual(modern._padding_pair(nav.pack_info()["padx"]), (12, 12))
        self.assertEqual(modern._padding_pair(nav.pack_info()["pady"]), (6, 6))

    def test_shared_palette_drives_docs_and_preview(self) -> None:
        light = modern.LIGHT_PALETTE
        self.assertEqual(self.app.ui_palette["field"], light["field"])
        self.assertEqual(self.app.ui_palette["text"], light["text"])

        doc = self.app.doc_text
        self.assertEqual(str(doc.tag_cget("doc_code_box", "background")),
                         light["surface_alt"])
        self.assertEqual(str(doc.tag_cget("doc_quote", "background")),
                         light["surface_alt"])
        self.assertEqual(str(doc.tag_cget("doc_link", "foreground")),
                         light["accent"])
        self.assertEqual(str(doc.tag_cget("doc_heading", "foreground")),
                         light["blue"])

        palette = self.app.output_preview._palette()
        self.assertEqual(palette["field"], light["field"])
        self.assertEqual(palette["text"], light["text"])

        self.app._toggle_modern_theme()
        dark = modern.DARK_PALETTE
        self.assertEqual(self.app.ui_palette["field"], dark["field"])
        self.assertEqual(str(self.app.doc_text.tag_cget("doc_code_box", "background")),
                         dark["surface_alt"])
        self.assertEqual(self.app.output_preview._palette()["field"], dark["field"])

    def test_checkboxes_get_large_rounded_indicators(self) -> None:
        from tkinter import ttk

        if modern.Image is None:
            self.skipTest("Pillow is not installed; square ttk fallback is expected")
        style = ttk.Style()
        layout = style.layout("TCheckbutton")
        self.assertEqual(layout[0][1]["children"][0][0],
                         self.app._rounded_key("Checkbutton.indicator"))
        self.assertEqual(str(style.lookup("TCheckbutton", "font")), modern.CHECK_FONT)
        side = self.app._px(22)
        indicators = [image for image in self.app._rounded_images
                      if image.width() == side and image.height() == side]
        self.assertGreaterEqual(len(indicators), 8)

    def test_gui_scale_affects_buttons_and_chrome(self) -> None:
        self.app.settings = dict(self.app.settings, ui_scale=1.5)
        self.app._apply_modern_theme(self.app.settings)

        self.assertEqual(self.app._modern_scale, 1.5)
        self.assertEqual(self.app._px(16), 24)
        self.assertEqual(self.app._rounded_images[0].width(),
                         self.app._px(48))
        self.assertEqual(self.app._accent_bar.cget("width"),
                         self.app._px(6))

    def test_window_size_does_not_follow_gui_scale(self) -> None:
        self.root.deiconify()
        self.root.update_idletasks()
        self.root.update()
        expected = (int(gui.SHIPPED_SETTINGS["window_width"]),
                    int(gui.SHIPPED_SETTINGS["window_height"]))
        self.assertEqual((self.root.winfo_width(), self.root.winfo_height()), expected)

        for scale in (1.5, 0.75):
            self.app._apply_ui_settings(dict(self.app.settings, ui_scale=scale))
            self.root.update_idletasks()
            self.root.update()
            self.assertEqual(
                (self.root.winfo_width(), self.root.winfo_height()), expected,
                f"window resized at ui_scale={scale}",
            )

    def test_theme_toggle_updates_palette_and_persists(self) -> None:
        self.assertFalse(bool(self.app.settings.get("dark_mode")))
        self.app._toggle_modern_theme()
        self.assertTrue(bool(self.app.settings.get("dark_mode")))
        self.assertEqual(self.app.modern_palette, modern.DARK_PALETTE)
        self.assertEqual(str(self.app._theme_button.cget("text")), "Light mode")
        saved = json.loads(gui.SETTINGS_FILE.read_text(encoding="utf-8"))
        self.assertTrue(saved["dark_mode"])

        self.app._toggle_modern_theme()
        self.assertFalse(bool(self.app.settings.get("dark_mode")))
        self.assertEqual(self.app.modern_palette, modern.LIGHT_PALETTE)
        self.assertEqual(str(self.app._theme_button.cget("text")), "Dark mode")


if __name__ == "__main__":
    unittest.main()
