#!/usr/bin/env python3
"""Modern-styled front end for the PyChrono excavator study workflow.

This file is intentionally separate from ``study_gui.py``. It subclasses the
existing GUI to reuse every wizard action, tool, and Docs page, then applies a
flat, accent-coloured skin on top. The original GUI files are left untouched.

Launch with ``./run_study_gui_modern.sh``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import tkinter as tk
from tkinter import Tk, ttk
from tkinter import font as tkfont

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import study_gui as gui  # noqa: E402

try:  # Pillow is used only for rounded ttk image elements; square is the fallback.
    from PIL import Image, ImageDraw, ImageTk
except Exception:  # pragma: no cover - exercised only on installs without Pillow
    Image = ImageDraw = ImageTk = None


DISPLAY_FONT = "ModernDisplay"
SUBTITLE_FONT = "ModernSubtitle"
TAB_FONT = "ModernTab"
BUTTON_FONT = "ModernButton"
CHECK_FONT = "ModernCheck"
BADGE_FONT = "ModernBadge"
SECTION_FONT = "ModernSection"

UI_FONT_CANDIDATES = (
    "Inter", "Segoe UI Variable", "Segoe UI", "Noto Sans",
    "Liberation Sans", "DejaVu Sans",
)
MONO_FONT_CANDIDATES = (
    "JetBrains Mono", "Cascadia Mono", "SF Mono", "DejaVu Sans Mono",
    "Liberation Mono",
)

LIGHT_PALETTE = {
    "bg": "#F5F6FA",
    "surface": "#FFFFFF",
    "surface_alt": "#EEF1F7",
    "field": "#FFFFFF",
    "border": "#DCE1EC",
    "text": "#172033",
    "muted": "#5F6B85",
    "accent": "#4F46E5",
    "accent_hover": "#4338CA",
    "accent_press": "#3730A3",
    "accent_soft": "#E9E8FF",
    "on_accent": "#FFFFFF",
    "hover": "#E4E8F2",
    "selected": "#D8DCF7",
    "danger": "#C62828",
    "danger_hover": "#9F1D1D",
    "warning": "#B45309",
    "warning_hover": "#92400E",
    "success": "#047857",
    "success_hover": "#065F46",
    "blue": "#0369A1",
    "blue_hover": "#075985",
    "blue_soft": "#E0F2FE",
    "teal": "#0F766E",
    "teal_hover": "#115E59",
    "teal_soft": "#CCFBF1",
    "violet": "#7C3AED",
    "violet_hover": "#6D28D9",
    "violet_soft": "#EDE9FE",
    "green_soft": "#D1FAE5",
}

def _modern_scale(values: dict[str, object]) -> float:
    """Read the GUI scale with the same validation as the classic GUI."""
    try:
        return gui._setting_scale(values.get("ui_scale"))
    except (TypeError, ValueError):
        return 1.0


def _padding_pair(value: object) -> tuple[int, int]:
    """Normalise Tk padx/pady values to a pair of whole pixels."""
    if isinstance(value, (tuple, list)):
        parts = value[:2]
    else:
        parts = str(value).replace(",", " ").strip("()[]{}").split()
    if not parts:
        return (0, 0)

    def pixels(part: object) -> int:
        try:
            return int(round(float(str(part).rstrip("p"))))
        except (TypeError, ValueError):
            return 0

    first = pixels(parts[0])
    second = pixels(parts[1]) if len(parts) > 1 else first
    return (first, second)


def _button_colors(p: dict[str, str]) -> dict[str, tuple[str, str, str, str]]:
    """Map button styles to (normal, hover, pressed, disabled) background colours."""
    return {
        "TButton": (p["surface_alt"], p["hover"], p["selected"], p["surface_alt"]),
        "Modern.Primary.TButton": (p["accent"], p["accent_hover"], p["accent_press"], p["border"]),
        "Modern.Soft.TButton": (p["accent_soft"], p["hover"], p["border"], p["surface_alt"]),
        "Modern.Info.TButton": (p["blue"], p["blue_hover"], p["border"], p["surface_alt"]),
        "Modern.Teal.TButton": (p["teal"], p["teal_hover"], p["border"], p["surface_alt"]),
        "Modern.Success.TButton": (p["success"], p["success_hover"], p["border"], p["surface_alt"]),
        "Modern.Danger.TButton": (p["danger"], p["danger_hover"], p["border"], p["surface_alt"]),
        "Modern.Warning.TButton": (p["warning"], p["warning_hover"], p["border"], p["surface_alt"]),
        "Modern.Ghost.TButton": (p["surface"], p["hover"], p["surface_alt"], p["surface"]),
        "Modern.Chip.TButton": (p["surface_alt"], p["hover"], p["selected"], p["surface_alt"]),
        "Modern.Chip.Active.TButton": (p["accent"], p["accent_hover"], p["accent_press"], p["border"]),
    }


DARK_PALETTE = {
    "bg": "#0E1118",
    "surface": "#171B26",
    "surface_alt": "#202636",
    "field": "#111620",
    "border": "#2D3649",
    "text": "#E7EBF5",
    "muted": "#9AA5BC",
    "accent": "#8B93FF",
    "accent_hover": "#A5ABFF",
    "accent_press": "#6D75EA",
    "accent_soft": "#252A47",
    "on_accent": "#101426",
    "hover": "#283044",
    "selected": "#323B55",
    "danger": "#FF8A80",
    "danger_hover": "#FFAB9F",
    "warning": "#FBBF24",
    "warning_hover": "#FCD34D",
    "success": "#34D399",
    "success_hover": "#6EE7B7",
    "blue": "#7DD3FC",
    "blue_hover": "#BAE6FD",
    "blue_soft": "#1B3350",
    "teal": "#5EEAD4",
    "teal_hover": "#99F6E4",
    "teal_soft": "#153B3B",
    "violet": "#C4B5FD",
    "violet_hover": "#DDD6FE",
    "violet_soft": "#2D2550",
    "green_soft": "#143D33",
}


def _pick_family(root: tk.Misc, candidates: tuple[str, ...], fallback: str) -> str:
    """Return the first installed font from *candidates*, or *fallback*."""
    try:
        families = set(tkfont.families(root))
    except tk.TclError:
        return fallback
    for name in candidates:
        if name in families:
            return name
    return fallback


def _named_font(root: tk.Misc, name: str, *, size: int,
                weight: str = "normal", slant: str = "roman") -> tkfont.Font:
    """Create or update a named font used by the modern styles.

    ``tkfont.Font`` deletes a named font when its last Python reference goes
    away, so keep the objects alive on the root.  Otherwise ttk keeps the font
    name but Tk falls back to a larger default font.
    """
    fonts = getattr(root, "_modern_fonts", None)
    if fonts is None:
        fonts = []
        root._modern_fonts = fonts
    try:
        font = tkfont.nametofont(name, root=root)
    except tk.TclError:
        font = tkfont.Font(root=root, name=name, exists=False)
    font.configure(family=_pick_family(root, UI_FONT_CANDIDATES, "sans-serif"),
                   size=size, weight=weight, slant=slant)
    if font not in fonts:
        fonts.append(font)
    return font


def _configure(style: ttk.Style, name: str, **options) -> None:
    """Apply style options one at a time; ignore unsupported Tk options."""
    for key, value in options.items():
        try:
            style.configure(name, **{key: value})
        except tk.TclError:
            pass


def _map(style: ttk.Style, name: str, **options) -> None:
    try:
        style.map(name, **options)
    except tk.TclError:
        pass


def _replace_layout_element(layout, old: str, new: str):
    """Swap a ttk layout element while preserving its children."""
    result = []
    for name, options in layout:
        if name == old:
            name = new
            options = dict(options)
            options.pop("border", None)  # the image element owns its border
        if "children" in options:
            options["children"] = _replace_layout_element(options["children"], old, new)
        result.append((name, options))
    return result


class ModernStudyGUI(gui.StudyGUI):
    """The full study GUI with a modern flat visual treatment."""

    def __init__(self, root: Tk):
        self._modern_header: ttk.Frame | None = None
        self._header_titles: ttk.Frame | None = None
        self._header_steps: ttk.Frame | None = None
        self._accent_bar: tk.Frame | None = None
        self._theme_button: ttk.Button | None = None
        self._step_chips: dict[str, ttk.Button] = {}
        self._modern_scale = 1.0
        self._modern_font_size = 10
        self._legacy_padding: list[tuple[object, str, tuple[int, int], tuple[int, int]]] = []
        self._rounded_images: list = []
        self._rounded_element_names: list[str] = []
        self._pristine_layouts: dict[str, object] = {}
        self._rounded_generation = 0
        self.modern_palette: dict[str, str] = {}
        self._modern_family = _pick_family(root, UI_FONT_CANDIDATES, "sans-serif")
        self._prepare_base_fonts(root)
        super().__init__(root)
        self._snapshot_legacy_padding()
        self._modern_scale = _modern_scale(self.settings)
        self._modern_font_size = int(self.settings.get("font_size", 10))
        self.root.title("PyChrono Excavator — Modern Studio")
        self._build_modern_chrome()
        self._apply_modern_theme(self.settings)
        self._apply_window_geometry(self.settings)

    # -- setup ---------------------------------------------------------
    def _prepare_base_fonts(self, root: tk.Misc) -> None:
        """Teach the inherited theme code our preferred font families."""
        mono = _pick_family(root, MONO_FONT_CANDIDATES, "monospace")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family=self._modern_family)
            except tk.TclError:
                pass
        try:
            tkfont.nametofont("TkFixedFont").configure(family=mono)
        except tk.TclError:
            pass

    def _build_modern_chrome(self) -> None:
        """Add a compact app header above the existing tab area."""
        header = ttk.Frame(self.root, style="Modern.Header.TFrame")
        header.pack(side=tk.TOP, fill=tk.X, before=self.notebook)

        accent = tk.Frame(header, width=self._px(6),
                          bg=self.modern_palette.get("accent", "#4F46E5"),
                          bd=0, highlightthickness=0)
        accent.pack(side=tk.LEFT, fill=tk.Y,
                    padx=(self._px(16), self._px(14)), pady=self._px(12))
        accent.pack_propagate(False)
        self._accent_bar = accent

        titles = ttk.Frame(header, style="Modern.Header.TFrame")
        titles.pack(side=tk.LEFT, fill=tk.Y,
                    pady=(self._px(11), self._px(10)))
        ttk.Label(titles, text="Excavator Study Studio",
                  style="Modern.Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text="CRM → one-way FEA workspace",
                  style="Modern.Subtitle.TLabel").pack(anchor="w")
        self._header_titles = titles

        steps = ttk.Frame(header, style="Modern.Header.TFrame")
        steps.pack(side=tk.RIGHT, padx=(0, self._px(14)))
        for key, number, label in (
            ("configure", 1, "Configure"),
            ("simulate", 2, "Simulate"),
            ("review", 3, "Review"),
        ):
            chip = ttk.Button(
                steps, text=f"{number}  {label}",
                style="Modern.Chip.TButton",
                command=lambda step=key: self._goto_step(step),
            )
            chip.pack(side=tk.LEFT, padx=(self._px(4), 0))
            self._step_chips[key] = chip
        self._header_steps = steps
        self.notebook.bind("<<NotebookTabChanged>>", self._update_step_chips, add="+")
        self.step_nb.bind("<<NotebookTabChanged>>", self._update_step_chips, add="+")

        self._theme_button = ttk.Button(
            header, text="Dark mode", style="Modern.Soft.TButton",
            command=self._toggle_modern_theme,
        )
        self._theme_button.pack(side=tk.RIGHT, padx=(0, self._px(10)))
        self._modern_header = header

    def _layout_modern_chrome(self) -> None:
        """Re-apply scaled header spacing after a GUI scale change."""
        if self._accent_bar is not None:
            try:
                self._accent_bar.configure(width=self._px(6))
            except tk.TclError:
                pass
            self._accent_bar.pack_configure(
                padx=(self._px(16), self._px(14)), pady=self._px(12))
        if self._header_titles is not None:
            self._header_titles.pack_configure(
                pady=(self._px(11), self._px(10)))
        if self._header_steps is not None:
            self._header_steps.pack_configure(padx=(0, self._px(14)))
            for chip in self._header_steps.winfo_children():
                chip.pack_configure(padx=(self._px(4), 0))
        if self._theme_button is not None:
            self._theme_button.pack_configure(padx=(0, self._px(10)))

    def _goto_step(self, step: str) -> None:
        """Jump a header chip to its configured view."""
        if step == "review":
            self.notebook.select(self.tools_tab)
        else:
            page = {"configure": 0, "simulate": 5}.get(step, 0)
            self.notebook.select(self.study_tab)
            try:
                self.step_nb.select(page)
            except tk.TclError:
                pass
        self._update_step_chips()

    def _update_step_chips(self, *_args) -> None:
        """Highlight the chip that matches the currently visible wizard stage."""
        if not self._step_chips:
            return
        active = ""
        try:
            selected = self.notebook.select()
        except tk.TclError:
            selected = ""
        if selected == str(self.study_tab):
            try:
                page = self.step_nb.index(self.step_nb.select())
                last_page = self.step_nb.index("end") - 1
            except (tk.TclError, ValueError):
                page = 0
                last_page = 0
            # Pages 1-5 are configuration; page 6 is Review & Run.
            if page < last_page:
                active = "configure"
            else:
                active = "simulate"
        elif selected in (str(self.tools_tab), str(getattr(self, "preview_tab", ""))):
            active = "review"
        for key, chip in self._step_chips.items():
            style = "Modern.Chip.Active.TButton" if key == active else "Modern.Chip.TButton"
            try:
                chip.configure(style=style)
            except tk.TclError:
                pass

    # -- theming -------------------------------------------------------
    def _apply_ui_settings(self, values: dict[str, object] | None = None) -> None:
        """Keep the modern skin applied even when inherited settings change."""
        values = values or self.settings
        super()._apply_ui_settings(values)
        self._apply_modern_theme(values)
        # The modern theme changes padding and image sizes after the classic
        # pass, so reassert the literal pixel window size once more.
        self._apply_window_geometry(values)

    def _palette(self, values: dict[str, object]) -> dict[str, str]:
        return dict(DARK_PALETTE if values.get("dark_mode") else LIGHT_PALETTE)

    def _sync_shared_palette(self, p: dict[str, str]) -> None:
        """Expose the modern palette to inherited classic helpers.

        ``ui_palette`` is the bridge used by the Docs tag configuration and by
        the Output Preview canvases.  Without this, those areas keep the
        classic black/grey palette even when the modern theme is active.
        """
        self.ui_palette = {
            "bg": p["bg"], "frame": p["surface"], "field": p["field"],
            "text": p["text"], "button": p["surface_alt"],
        }

    def _apply_modern_doc_styles(self, p: dict[str, str]) -> None:
        """Recolour Docs text tags and embedded tables with the modern palette."""
        doc_text = getattr(self, "doc_text", None)
        if doc_text is None:
            return
        # Re-run the classic pass so tag fonts/spacing stay in sync, then
        # replace the colours that the classic pass hard-codes.
        self._configure_doc_tags()
        foregrounds = {
            "doc_bold": p["text"],
            "doc_italic": p["text"],
            "doc_title": p["violet"],
            "doc_heading": p["blue"],
            "doc_subheading": p["teal"],
            "doc_inline_code": p["accent"],
            "doc_link": p["accent"],
            "doc_code": p["text"],
            "doc_code_box": p["text"],
            "doc_table": p["text"],
            "doc_table_header": p["blue"],
            "doc_table_bold": p["text"],
            "doc_table_separator": p["muted"],
            "doc_quote": p["text"],
            "doc_quote_bar": p["teal"],
            "doc_rule": p["muted"],
        }
        for tag, color in foregrounds.items():
            doc_text.tag_configure(tag, foreground=color)
        for tag in ("doc_inline_code", "doc_code", "doc_code_box", "doc_quote"):
            doc_text.tag_configure(tag, background=p["surface_alt"])
        for tag in getattr(self, "_doc_link_tags", {}).values():
            doc_text.tag_configure(tag, foreground=p["accent"], underline=1)
        try:
            doc_text.tag_raise("doc_search")
        except tk.TclError:
            pass

        style = ttk.Style()
        _configure(style, "DocTable.Treeview", background=p["field"],
                   fieldbackground=p["field"], foreground=p["text"],
                   bordercolor=p["border"])
        _configure(style, "DocTable.Treeview.Heading",
                   background=p["surface_alt"], foreground=p["blue"],
                   font=gui.DOC_TABLE_HEADER_FONT)
        _map(style, "DocTable.Treeview.Heading",
             background=[("active", p["hover"])])
        _map(style, "DocTable.Treeview",
             background=[("selected", p["accent_soft"])],
             foreground=[("selected", p["text"])])

    def _refresh_modern_preview(self) -> None:
        """Redraw the preview canvases using the modern shared palette."""
        preview = getattr(self, "output_preview", None)
        if preview is None:
            return
        try:
            preview.refresh()
        except (tk.TclError, OSError, ValueError):
            pass

    def _px(self, value: float) -> int:
        """Scale a pixel measurement with the GUI scale setting."""
        return max(1, int(round(value * self._modern_scale)))

    def _px_pair(self, pair: tuple[int, int]) -> tuple[int, int]:
        return (self._px(pair[0]), self._px(pair[1]))

    def _snapshot_legacy_padding(self) -> None:
        """Remember the classic layout's fixed pack/grid padding.

        The classic GUI uses literal pixel padding on its navigation bars,
        toolbars, and buttons.  Saving the original values here lets the modern
        skin reapply them at the current GUI scale without changing
        ``study_gui.py``.
        """
        for widget in self._walk_widgets():
            manager = widget.winfo_manager()
            if manager == "pack":
                info = widget.pack_info()
            elif manager == "grid":
                info = widget.grid_info()
            else:
                continue
            padx = _padding_pair(info.get("padx", 0))
            pady = _padding_pair(info.get("pady", 0))
            if padx == (0, 0) and pady == (0, 0):
                continue
            self._legacy_padding.append((widget, manager, padx, pady))

    def _apply_legacy_padding_scale(self) -> None:
        """Reapply the snapshot padding using the current GUI scale."""
        for widget, manager, padx, pady in self._legacy_padding:
            try:
                if manager == "pack":
                    widget.pack_configure(padx=self._px_pair(padx),
                                          pady=self._px_pair(pady))
                else:
                    widget.grid_configure(padx=self._px_pair(padx),
                                          pady=self._px_pair(pady))
            except tk.TclError:
                continue

    # -- rounded ttk elements ------------------------------------------
    def _rounded_key(self, name: str) -> str:
        """Return a unique element name for this theme generation."""
        return f"Modern.G{self._rounded_generation}.{name}"

    def _rounded_name(self, style_name: str, suffix: str = "border") -> str:
        return self._rounded_key(style_name.replace(".", "_") + "." + suffix)

    def _rounded_photo(self, fill: str, outline: str, *, width: int = 64,
                       height: int = 32, radius: int = 10) -> object:
        """Return a PhotoImage of a flat rounded rectangle with a thin outline."""
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        ImageDraw.Draw(image).rounded_rectangle(
            (0, 0, width - 1, height - 1), radius=radius,
            fill=fill, outline=outline, width=2,
        )
        photo = ImageTk.PhotoImage(image, master=self.root)
        self._rounded_images.append(photo)
        return photo

    def _checkbox_photo(self, fill: str, outline: str, *,
                        size: int, mark: str | None = None,
                        mark_color: str = "#FFFFFF") -> object:
        """Return a rounded checkbox indicator image, optionally checked."""
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        width = max(1, size // 12)
        draw.rounded_rectangle(
            (width, width, size - width - 1, size - width - 1),
            radius=max(2, size // 5), fill=fill, outline=outline, width=width,
        )
        if mark == "check":
            points = [(size * 0.20, size * 0.51),
                      (size * 0.41, size * 0.73),
                      (size * 0.80, size * 0.27)]
            draw.line([(int(x), int(y)) for x, y in points],
                      fill=mark_color, width=max(2, size // 8), joint="curve")
        elif mark == "dash":
            draw.line([(int(size * 0.25), int(size * 0.5)),
                       (int(size * 0.75), int(size * 0.5))],
                      fill=mark_color, width=max(2, size // 8))
        photo = ImageTk.PhotoImage(image, master=self.root)
        self._rounded_images.append(photo)
        return photo

    def _create_image_element(self, style: ttk.Style, name: str,
                              states: list[tuple[str | None, str, str]],
                              *, radius: int = 10, width: int = 64,
                              height: int = 32) -> None:
        """Create a ttk image element whose colours follow widget state."""
        options: list[object] = []
        for state, fill, outline in states:
            photo = self._rounded_photo(fill, outline, width=width,
                                        height=height, radius=radius)
            if state is None:
                options.append(str(photo))
            else:
                options.append((state, str(photo)))
        style.element_create(name, "image", *options,
                             border=(radius, radius, radius, radius),
                             sticky="nsew")
        self._rounded_element_names.append(name)

    def _create_rounded_elements(self, style: ttk.Style,
                                 p: dict[str, str]) -> None:
        """Build rounded image elements for buttons, fields, tabs, and frames."""
        if Image is None:
            return
        for style_name, (normal, hover, pressed, disabled) in _button_colors(p).items():
            self._create_image_element(
                style, self._rounded_name(style_name),
                [(None, normal, normal),
                 ("active", hover, hover),
                 ("pressed", pressed, pressed),
                 ("disabled", disabled, disabled)],
                # A smaller corner radius matters here: the image element's
                # border is added to the widget's requested size, so a 10px
                # radius made every button 20px taller than its text needed.
                radius=self._px(6),
                width=self._px(48), height=self._px(24),
            )

        self._create_image_element(
            style, self._rounded_key("Entry.field"),
            [(None, p["field"], p["border"]),
             ("focus", p["field"], p["accent"]),
             ("disabled", p["surface_alt"], p["border"])],
            radius=self._px(5),
            width=self._px(48), height=self._px(24),
        )
        self._create_image_element(
            style, self._rounded_key("Combobox.field"),
            [(None, p["field"], p["border"]),
             ("focus", p["field"], p["accent"]),
             ("readonly", p["field"], p["border"]),
             ("disabled", p["surface_alt"], p["border"])],
            radius=self._px(5),
            width=self._px(48), height=self._px(24),
        )
        self._create_image_element(
            style, self._rounded_key("Spinbox.field"),
            [(None, p["field"], p["border"]),
             ("focus", p["field"], p["accent"]),
             ("disabled", p["surface_alt"], p["border"])],
            radius=self._px(5),
            width=self._px(48), height=self._px(24),
        )
        self._create_image_element(
            style, self._rounded_key("Labelframe.border"),
            [(None, p["surface"], p["border"])],
            radius=self._px(12), width=self._px(96), height=self._px(48),
        )
        # A single image for every tab state keeps selected and unselected tabs
        # exactly the same size.  ttk draws state-specific tab images at their
        # natural size, which made the selected tab shrink.  Selection is shown
        # by the foreground colour and the corner background instead.
        self._create_image_element(
            style, self._rounded_key("Notebook.tab"),
            [(None, p["surface_alt"], p["border"])],
            # Keep tabs slim: the image border is added to the requested size,
            # so a 10px radius was making every tab much taller than its text.
            radius=self._px(4), width=self._px(120), height=self._px(24),
        )
        # A larger rounded indicator with a drawn check/dash makes checkboxes
        # easier to spot than the tiny square supplied by the stock theme.
        check_name = self._rounded_key("Checkbutton.indicator")
        check_size = self._px(22)
        check_states = (
            (None, p["field"], p["border"], None, p["muted"]),
            ("active", p["field"], p["accent"], None, p["muted"]),
            ("selected", p["accent"], p["accent"], "check", p["on_accent"]),
            ("selected active", p["accent_hover"], p["accent_hover"], "check", p["on_accent"]),
            ("alternate", p["accent"], p["accent"], "dash", p["on_accent"]),
            ("disabled", p["surface_alt"], p["border"], None, p["muted"]),
            ("selected disabled", p["border"], p["border"], "check", p["surface"]),
            ("alternate disabled", p["border"], p["border"], "dash", p["surface"]),
        )
        check_options: list[object] = []
        for state, fill, outline, mark, mark_color in check_states:
            photo = self._checkbox_photo(
                fill, outline, size=check_size, mark=mark,
                mark_color=mark_color,
            )
            check_options.append(str(photo) if state is None else (state, str(photo)))
        style.element_create(check_name, "image", *check_options, sticky="")
        self._rounded_element_names.append(check_name)

    def _apply_rounded_layouts(self, style: ttk.Style) -> None:
        """Swap square ttk borders for the rounded image elements."""
        if Image is None:
            return
        # Capture the stock layouts once.  Later theme changes must start from
        # these, not from an already-rounded layout, or the swap stops working.
        # A cached layout may still name an element from an older generation, so
        # translate those names back to their stock ttk equivalents first.
        def stock_element(name: object) -> object:
            if not isinstance(name, str) or not name.startswith("Modern.G"):
                return name
            parts = name.split(".", 2)
            if len(parts) < 3:
                return name
            suffix = parts[2]
            return "Button.border" if suffix.endswith("Button.border") else suffix

        def stock_layout(layout):
            result = []
            for name, options in layout:
                name = stock_element(name)
                if "children" in options:
                    options = dict(options)
                    options["children"] = stock_layout(options["children"])
                result.append((name, options))
            return result

        def pristine(key: str):
            if key not in self._pristine_layouts:
                self._pristine_layouts[key] = stock_layout(style.layout(key))
            return stock_layout(self._pristine_layouts[key])

        # Each button style needs its own layout so its coloured image is used;
        # a shared base layout would make every button the same colour.
        for style_name in _button_colors(self.modern_palette):
            new = self._rounded_name(style_name)
            try:
                style.layout(style_name, _replace_layout_element(
                    pristine("TButton"), "Button.border", new))
            except tk.TclError:
                continue

        replacements = (
            ("TEntry", "Entry.field", self._rounded_key("Entry.field")),
            ("TCombobox", "Combobox.field", self._rounded_key("Combobox.field")),
            ("TSpinbox", "Spinbox.field", self._rounded_key("Spinbox.field")),
            ("TLabelframe", "Labelframe.border", self._rounded_key("Labelframe.border")),
            ("TNotebook.Tab", "Notebook.tab", self._rounded_key("Notebook.tab")),
            ("TCheckbutton", "Checkbutton.indicator",
             self._rounded_key("Checkbutton.indicator")),
        )
        for base, original, new in replacements:
            try:
                style.layout(base, _replace_layout_element(
                    pristine(base), original, new))
            except tk.TclError:
                continue

    def _clear_rounded_elements(self) -> None:
        """Drop references to the previous theme's images and element names.

        Tk has no portable ``Style.element_delete`` in all supported versions,
        so each theme generation gets fresh, uniquely named elements instead.
        """
        self._rounded_generation += 1
        self._rounded_element_names: list[str] = []
        self._rounded_images: list = []

    def _apply_modern_theme(self, values: dict[str, object] | None = None) -> None:
        values = values or self.settings
        p = self._palette(values)
        self.modern_palette = p
        self._sync_shared_palette(p)
        try:
            size = int(values.get("font_size", 10))
        except (TypeError, ValueError):
            size = 10
        self._modern_font_size = size
        self._modern_scale = _modern_scale(values)

        self._clear_rounded_elements()
        style = ttk.Style()
        # "clam" honours explicit colours on every supported Tk build.
        if "clam" in style.theme_names():
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass

        # Keep the modern chrome deliberately compact; the classic GUI owns the
        # base point sizes, and these offsets avoid oversized bars and buttons.
        _named_font(self.root, DISPLAY_FONT, size=size + 4, weight="bold")
        _named_font(self.root, SUBTITLE_FONT, size=max(7, size - 2))
        _named_font(self.root, TAB_FONT, size=max(7, size - 2), weight="bold")
        _named_font(self.root, BUTTON_FONT, size=max(7, size - 2), weight="bold")
        _named_font(self.root, CHECK_FONT, size=max(9, size), weight="bold")
        _named_font(self.root, BADGE_FONT, size=max(6, size - 3), weight="bold")
        _named_font(self.root, SECTION_FONT, size=max(8, size), weight="bold")

        self._create_rounded_elements(style, p)
        self._configure_core_styles(style, p, size)
        self._configure_button_styles(style, p)
        self._configure_data_styles(style, p, size)
        self._apply_rounded_layouts(style)
        self._restyle_existing_widgets(p)
        self._apply_modern_doc_styles(p)
        self._refresh_modern_preview()
        self._layout_modern_chrome()
        self._apply_legacy_padding_scale()
        self._update_step_chips()

    def _configure_core_styles(self, style: ttk.Style, p: dict[str, str],
                               size: int) -> None:
        _configure(style, "TFrame", background=p["bg"])
        _configure(style, "TLabel", background=p["bg"], foreground=p["text"])

        _configure(style, "Modern.Header.TFrame", background=p["surface"])
        _configure(style, "Modern.Title.TLabel", background=p["surface"],
                   foreground=p["text"], font=DISPLAY_FONT)
        _configure(style, "Modern.Subtitle.TLabel", background=p["surface"],
                   foreground=p["muted"], font=SUBTITLE_FONT)
        _configure(style, "Modern.Chip.TLabel", background=p["surface_alt"],
                   foreground=p["muted"], font=BADGE_FONT,
                   padding=(self._px(10), self._px(4)))
        _configure(style, "Modern.Muted.TLabel", background=p["bg"],
                   foreground=p["muted"], font=SUBTITLE_FONT)

        _configure(style, "TLabelframe", background=p["surface"],
                   foreground=p["text"], bordercolor=p["border"],
                   relief="flat", borderwidth=1,
                   padding=(self._px(12), self._px(10),
                            self._px(12), self._px(12)))
        _configure(style, "TLabelframe.Label", background=p["surface"],
                   foreground=p["accent"], font=SECTION_FONT)

        margins = (self._px(10), self._px(8), self._px(10), 0)
        _configure(style, "TNotebook", background=p["bg"],
                   borderwidth=0, tabmargins=margins)
        _configure(style, "Modern.TNotebook", background=p["bg"],
                   borderwidth=0, tabmargins=margins)
        # The wizard's inner notebook uses TNotebook.Tab; the top-level tabs use
        # Modern.TNotebook.Tab.  One shared padding for every state keeps the
        # selected tab exactly the same width as its neighbours.  Selection is
        # communicated by colours, never by a different image or padding.
        tab_pad = (self._px(8), self._px(4))
        for tab_style in ("TNotebook.Tab", "Modern.TNotebook.Tab"):
            _configure(style, tab_style, background=p["surface_alt"],
                       foreground=p["muted"], font=TAB_FONT,
                       borderwidth=0, padding=tab_pad)
            _map(style, tab_style,
                 background=[("selected", p["accent_soft"]),
                             ("active", p["hover"]),
                             ("disabled", p["surface_alt"])],
                 foreground=[("selected", p["accent"]),
                             ("disabled", p["border"])],
                 # The stock theme narrows the selected tab; clear that map so
                 # every tab keeps the same configured width.
                 padding=[])

        field_pad = (self._px(6), self._px(3))
        _configure(style, "TEntry", fieldbackground=p["field"],
                   foreground=p["text"], insertcolor=p["text"],
                   bordercolor=p["border"], relief="flat", padding=field_pad)
        for name in ("TCombobox", "TSpinbox"):
            _configure(style, name, fieldbackground=p["field"],
                       foreground=p["text"], insertcolor=p["text"],
                       bordercolor=p["border"], arrowcolor=p["muted"],
                       relief="flat", padding=field_pad)
        _map(style, "TCombobox",
             fieldbackground=[("readonly", p["field"]),
                              ("disabled", p["surface_alt"])],
             foreground=[("readonly", p["text"]), ("disabled", p["muted"])])

        _configure(style, "TCheckbutton", background=p["bg"],
                   foreground=p["text"], font=CHECK_FONT,
                   padding=(self._px(8), self._px(5)),
                   # Fallback for installs without Pillow; the image element
                   # supplied above ignores these indicator options.
                   indicatorsize=self._px(18), indicatormargin=self._px(3),
                   indicatorbackground=p["field"], indicatorforeground=p["accent"])
        _configure(style, "TRadiobutton", background=p["bg"],
                   foreground=p["text"], font=CHECK_FONT,
                   padding=(self._px(8), self._px(5)),
                   indicatorsize=self._px(18), indicatormargin=self._px(3),
                   indicatorbackground=p["field"], indicatorforeground=p["accent"])
        _configure(style, "TScale", background=p["bg"],
                   troughcolor=p["border"])
        _configure(style, "TScrollbar", background=p["surface_alt"],
                   troughcolor=p["bg"], arrowcolor=p["muted"],
                   borderwidth=0, relief="flat")

        for pattern, value in (
            ("*ComboboxPopdown*background", p["bg"]),
            ("*ComboboxPopdown*Listbox*background", p["field"]),
            ("*ComboboxPopdown*Listbox*foreground", p["text"]),
            ("*ComboboxPopdown*Listbox*selectBackground", p["accent"]),
            ("*ComboboxPopdown*Listbox*selectForeground", p["on_accent"]),
        ):
            try:
                self.root.option_add(pattern, value)
            except tk.TclError:
                pass

    def _configure_button_styles(self, style: ttk.Style,
                                 p: dict[str, str]) -> None:
        """Give each action family its own hue while keeping corners rounded.

        When the rounded image elements are active they draw the button body,
        so the ttk background must match the surrounding surface instead of the
        button colour.  Otherwise the square background would show through the
        transparent corners.
        """
        rounded = Image is not None
        foregrounds = {
            "TButton": p["text"],
            "Modern.Primary.TButton": p["on_accent"],
            "Modern.Soft.TButton": p["violet"],
            "Modern.Ghost.TButton": p["muted"],
            "Modern.Chip.TButton": p["muted"],
            "Modern.Chip.Active.TButton": p["on_accent"],
        }
        paddings = {
            "TButton": (self._px(6), self._px(4)),
            "Modern.Primary.TButton": (self._px(8), self._px(5)),
            "Modern.Soft.TButton": (self._px(5), self._px(3)),
            "Modern.Chip.TButton": (self._px(8), self._px(3)),
            "Modern.Chip.Active.TButton": (self._px(8), self._px(3)),
        }
        default_pad = (self._px(5), self._px(3))
        for name, (normal, hover, pressed, disabled) in _button_colors(p).items():
            background = p["bg"] if rounded else normal
            _configure(style, name, background=background,
                       foreground=foregrounds.get(name, p["on_accent"]),
                       font=BUTTON_FONT, borderwidth=0, relief="flat",
                       padding=paddings.get(name, default_pad))
            if rounded:
                states = [("active", p["bg"]), ("pressed", p["bg"]),
                          ("disabled", p["bg"])]
            else:
                states = [("active", hover), ("pressed", pressed),
                          ("disabled", disabled)]
            _map(style, name, background=states,
                 foreground=[("disabled", p["muted"])])

    def _configure_data_styles(self, style: ttk.Style, p: dict[str, str],
                               size: int) -> None:
        row_height = max(self._px(22), int(size * 2.1))
        _configure(style, "Treeview", background=p["field"],
                   fieldbackground=p["field"], foreground=p["text"],
                   bordercolor=p["border"], rowheight=row_height)
        _configure(style, "Treeview.Heading", background=p["surface_alt"],
                   foreground=p["text"], font=BUTTON_FONT,
                   borderwidth=0, relief="flat",
                   padding=(self._px(8), self._px(6)))
        _map(style, "Treeview",
             background=[("selected", p["accent_soft"])],
             foreground=[("selected", p["text"])])
        _configure(style, "TProgressbar", background=p["accent"],
                   troughcolor=p["surface_alt"], thickness=8,
                   borderwidth=0, relief="flat")

    def _restyle_existing_widgets(self, p: dict[str, str]) -> None:
        """Push the palette onto widgets ttk styles cannot reach."""
        self.root.configure(background=p["bg"])
        if self._accent_bar is not None:
            try:
                self._accent_bar.configure(bg=p["accent"])
            except tk.TclError:
                pass
        for widget in self._walk_widgets():
            try:
                wclass = widget.winfo_class()
                doc_path_var = getattr(self, "doc_path_var", None)
                if (isinstance(widget, ttk.Label) and doc_path_var is not None
                        and str(widget.cget("textvariable")) == str(doc_path_var)):
                    widget.configure(style="Modern.Muted.TLabel")
                elif wclass == "Text":
                    body_font = "TkDefaultFont" if widget is getattr(self, "doc_text", None) else "TkFixedFont"
                    widget.configure(
                        background=p["field"], foreground=p["text"],
                        insertbackground=p["accent"], font=body_font,
                        selectbackground=p["accent_soft"],
                        selectforeground=p["text"],
                        highlightthickness=1, highlightbackground=p["border"],
                        padx=self._px(10), pady=self._px(8),
                    )
                elif wclass == "Canvas":
                    widget.configure(background=p["bg"], highlightthickness=0)
                elif isinstance(widget, ttk.Notebook):
                    widget.configure(style="Modern.TNotebook")
            except (tk.TclError, AttributeError):
                continue
        self._style_action_buttons()
        self._update_theme_button()

    def _style_action_buttons(self) -> None:
        """Assign a distinct hue to each kind of action."""
        styles = {
            # Launching a simulation is the headline action.
            "Run study": "Modern.Primary.TButton",
            "Apply now": "Modern.Primary.TButton",
            # Secondary simulation / recalculation actions.
            "Run one-way FEA only (existing CRM)": "Modern.Teal.TButton",
            "Refresh preview": "Modern.Teal.TButton",
            "Preview STEP mesh (full-run mesher + comparison)": "Modern.Teal.TButton",
            "Open live 3D detector sizer": "Modern.Teal.TButton",
            "Live 3D fill-detector size adjuster": "Modern.Teal.TButton",
            "Apply to current study": "Modern.Teal.TButton",
            # File, navigation, and informational actions.
            "Load study_config.json": "Modern.Info.TButton",
            "Browse": "Modern.Info.TButton",
            "Pick...": "Modern.Info.TButton",
            "Find": "Modern.Info.TButton",
            "Refresh": "Modern.Info.TButton",
            "Open lightweight CRM/FEA output preview": "Modern.Info.TButton",
            "View geometry comparison (STEP vs converted)": "Modern.Info.TButton",
            "Copy current study values": "Modern.Info.TButton",
            # Saving / configuration changes.
            "Save config/commands (dry run)": "Modern.Soft.TButton",
            "Apply CAD/mesh defaults": "Modern.Soft.TButton",
            "Save defaults": "Modern.Soft.TButton",
            "Save settings": "Modern.Soft.TButton",
            # Viewing results is a positive, success-toned action.
            "Open interactive result plots": "Modern.Success.TButton",
            "Open study folder": "Modern.Success.TButton",
            # Low-emphasis actions.
            "Info": "Modern.Ghost.TButton",
            "< Back": "Modern.Ghost.TButton",
            "Next >": "Modern.Ghost.TButton",
            "Reset to shipped defaults": "Modern.Ghost.TButton",
            "Reset to defaults": "Modern.Ghost.TButton",
            # Process control.
            "Stop running process": "Modern.Danger.TButton",
            "Skip current step": "Modern.Warning.TButton",
        }
        chip_widgets = set(self._step_chips.values())
        for widget in self._walk_widgets():
            if (not isinstance(widget, ttk.Button) or widget is self._theme_button
                    or widget in chip_widgets):
                continue
            text = str(widget.cget("text"))
            named = styles.get(text, "TButton")
            try:
                widget.configure(style=named)
            except tk.TclError:
                pass

    # -- appearance controls -------------------------------------------
    def _update_theme_button(self) -> None:
        if self._theme_button is None:
            return
        dark = bool(self.settings.get("dark_mode"))
        try:
            self._theme_button.configure(text="Light mode" if dark else "Dark mode")
        except tk.TclError:
            pass

    def _toggle_modern_theme(self) -> None:
        """Switch light/dark mode and persist the choice like the Settings tab."""
        dark = not bool(self.settings.get("dark_mode"))
        self.settings = dict(self.settings)
        self.settings["dark_mode"] = dark
        var = getattr(self, "settings_vars", {}).get("dark_mode")
        if var is not None:
            try:
                var.set(dark)
            except tk.TclError:
                pass
        self._apply_ui_settings(self.settings)
        try:
            gui.write_ui_settings(self.settings)
        except (OSError, ValueError, tk.TclError):
            pass
        self.status_var.set(
            f"{'Dark' if dark else 'Light'} theme applied and saved."
        )


def main() -> None:
    root = tk.Tk()
    ModernStudyGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
