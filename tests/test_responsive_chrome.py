"""Static guards over the chrome that changes shape with the viewport.

Both rules here come from bugs that shipped and were only found by measuring a
real browser at real widths -- neither breaks a page, raises anything, or fails
any other test. They are asserted statically so a template nobody screenshots
is covered too.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"

COMMENT = re.compile(r"\{#.*?#\}")
# A label the small screen hides, leaving whatever icon sits beside it.
HIDDEN_LABEL = re.compile(r'<span class="hidden sm:(?:inline|block|flex)[^"]*">')
CLICKABLE_OPEN = re.compile(r"<(?:button|a)\b[^>]*>", re.S)
NEGATIVE_VERTICAL_MARGIN = re.compile(r'class="[^"]*?(-m[bt]-[\w.[\]]+|-my-[\w.[\]]+)')


def _templates():
    return sorted(TEMPLATES.rglob("*.html"))


def _uncommented(path):
    """The template with its `{# #}` comments removed.

    The comments explain these very rules, so scanning them would have every
    template that documents the trap fail the test for it.
    """
    return COMMENT.sub("", path.read_text())


def test_there_are_templates_to_check():
    """Guards the guard: an empty sweep passes everything below in silence."""
    assert len(_templates()) > 20


def test_global_loading_overlay_has_responsive_size_constraints():
    source = (TEMPLATES / "app.html").read_text()
    css = (ROOT / "assets" / "input.css").read_text()

    assert "global-loading-spinner" in source
    assert "width: min(" in css
    assert "env(safe-area-inset-top)" in css
    assert "env(safe-area-inset-left)" in css


# --- an icon that replaces a word has to say the word ---


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_a_label_hidden_on_small_screens_leaves_an_accessible_name(path):
    """`hidden sm:inline` on a button's text must leave an `aria-label` behind.

    The bar on the roster page carries seven controls. At 390px the roster name
    was down to 38px and at 320px to nothing at all, so `Trade`, `History` and
    `Sign out` became icons below `sm`. That is fine for the eye and silent for
    everything else: with the words display:none, a screen reader reaching the
    button finds an `<svg aria-hidden>` and no name whatsoever.

    Checked by finding the nearest clickable element opening before the hidden
    label, which is the element whose accessible name the label was providing.
    """
    source = _uncommented(path)
    offenders = []

    for match in HIDDEN_LABEL.finditer(source):
        opens = list(CLICKABLE_OPEN.finditer(source[: match.start()]))
        if not opens:
            continue  # A hidden label that is not inside a control at all.
        tag = opens[-1].group(0)
        if "aria-label" not in tag:
            line = source[: match.start()].count("\n") + 1
            offenders.append((line, " ".join(tag.split())[:70]))

    assert not offenders, (
        f"{path.relative_to(ROOT)} hides a control's text below `sm` without an "
        f"aria-label, so the icon that replaces it has no accessible name: {offenders}"
    )


# --- a negative vertical margin does not trim a `space-y` gap ---


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_no_negative_vertical_margins(path):
    """Tailwind v4 makes these silently destructive, so the repo does without.

    v4 compiles `space-y-6` to a `margin-block-end` on each non-last child,
    inside a `:where()` that carries no specificity. Any `mb-*` on such a child
    therefore *replaces* the gap instead of adjusting it -- `-mb-2`, written to
    pull an element closer to the card below it, set the gap to minus eight
    pixels and the card rendered on top of the text. Nothing errors; the words
    are simply covered.

    A blanket rule because the damage depends on the parent, which is not
    visible from the element's own class list. If a negative vertical margin is
    ever genuinely needed, the honest move is to name the exception here.
    """
    source = _uncommented(path)
    offenders = [
        (source[: m.start()].count("\n") + 1, m.group(1))
        for m in NEGATIVE_VERTICAL_MARGIN.finditer(source)
    ]

    assert not offenders, (
        f"{path.relative_to(ROOT)} uses a negative vertical margin: {offenders}. "
        f"Inside a `space-y-*` parent this replaces the gap rather than trimming "
        f"it. Use a positive `mb-*`/`mt-*`, which overrides it to a real value."
    )


# --- phones: every action reachable, no zoom on focus, reasons in words ---------


HEADER_BLOCK = re.compile(r"\{% block header_actions %\}(.*?)\{% endblock %\}", re.S)
FIELD_TAG = re.compile(r"<(?:input|select|textarea)\b[^>]*>", re.S)
DISABLED_BUTTON = re.compile(r"(<button\b[^>]*\bdisabled\b[^>]*>)(.*?)</button>", re.S)


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_a_header_action_hidden_on_phones_has_a_phone_alternative(path):
    """`hidden sm:inline-flex` in the header once left phones without "New season",
    "Invite" or "New profile". A page may still hide a labelled button below `sm`
    -- but only next to the arrow of `partials/back_link.html`, which is the
    phone's way back."""
    source = _uncommented(path)
    for block in HEADER_BLOCK.findall(source):
        if "hidden sm:inline-flex" in block or "hidden sm:flex" in block:
            assert "{% block back_link %}" in source, (
                f"{path.relative_to(ROOT)} hides a header action below `sm` with no "
                "phone alternative -- use partials/header_action.html or back_link.html"
            )


def test_shared_fields_are_16px_on_phones():
    """Below 16px iOS Safari zooms the page on every focus."""
    forms = (ROOT / "apps" / "core" / "forms.py").read_text()
    assert "text-base sm:text-sm" in forms


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_no_field_is_smaller_than_16px_on_phones(path):
    source = _uncommented(path)
    offenders = []
    for match in FIELD_TAG.finditer(source):
        tag = match.group(0)
        if any(kind in tag for kind in ('type="hidden"', 'type="checkbox"', 'type="radio"')):
            continue
        if re.search(r"\btext-(?:xs|sm)\b", tag) and "text-base" not in tag:
            offenders.append(source[: match.start()].count("\n") + 1)
    assert not offenders, (
        f"{path.relative_to(ROOT)}: fields below 16px on phones (iOS zooms in) "
        f"on lines {offenders}; use `text-base sm:text-sm`"
    )


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_a_disabled_button_does_not_explain_itself_only_in_a_tooltip(path):
    """A `title` never reaches a phone. A disabled button needs visible text,
    `aria-describedby` pointing at visible text, or an `aria-label`."""
    source = _uncommented(path)
    offenders = []
    for match in DISABLED_BUTTON.finditer(source):
        tag, inner = match.group(1), match.group(2)
        if "title=" not in tag:
            continue
        visible = re.sub(r"<svg.*?</svg>|<[^>]+>", "", inner, flags=re.S).strip()
        if not visible and "aria-describedby" not in tag and "aria-label" not in tag:
            offenders.append(source[: match.start()].count("\n") + 1)
    assert not offenders, f"{path.relative_to(ROOT)}: title-only disabled buttons on {offenders}"


def test_dialogs_are_bottom_sheets_on_phones():
    css = (ROOT / "assets" / "input.css").read_text()
    panel = css[css.index(".modal-panel {") : css.index("}", css.index(".modal-panel {"))]
    actions = css[css.index(".modal-actions {") : css.index("}", css.index(".modal-actions {"))]

    assert "max-sm:max-h-[90dvh]" in panel
    assert "max-sm:sticky" in actions
    assert "env(safe-area-inset-bottom)" in actions


def test_the_viewport_allows_safe_area_padding():
    assert "viewport-fit=cover" in (TEMPLATES / "base.html").read_text()
