"""Phase 1 of the mobile plan (my_docs/MOBILE_EXP.md), checked in a real browser
at phone width: navigation, header actions, fields, dialogs, touch targets."""

import pytest
from django.urls import reverse

from .conftest import page_overflow, shot
from .test_baseline import key_pages

# Phase 2 reworked the last wide tables: no page may scroll sideways any more.
PHASE_2_PAGES = set()

SMALL_TARGETS_JS = """
(selector) => [...document.querySelectorAll(selector)]
  .filter(el => {
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && style.visibility !== 'hidden'
      && !el.closest('[inert]');
  })
  .map(el => {
    const r = el.getBoundingClientRect();
    // A pseudo-element tap area (after:-inset-3) counts as part of the target.
    const after = getComputedStyle(el, '::after');
    const grow = after.position === 'absolute' ? Math.max(0, -parseFloat(after.left || 0)) * 2 : 0;
    return {
      text: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 40),
      width: Math.round(r.width + grow), height: Math.round(r.height + grow),
    };
  })
  .filter(t => t.width < 40 || t.height < 40)
"""


def test_no_page_scrolls_sideways_on_a_phone(game, open_page):
    problems = []
    for name, user, path in key_pages(game):
        overflow = page_overflow(open_page(user, path))
        if overflow > 0 and name not in PHASE_2_PAGES:
            problems.append((name, overflow))
    assert not problems, f"pages that scroll sideways at 375px: {problems}"


def test_the_menu_is_skipped_by_the_tab_key_until_opened(game, open_page):
    page = open_page(game["user"], reverse("core:dashboard"))

    for _ in range(6):
        page.keyboard.press("Tab")
        assert not page.evaluate("document.activeElement.closest('#app-nav') !== null"), (
            "the closed menu took keyboard focus"
        )

    menu_button = page.get_by_role("button", name="Toggle navigation")
    menu_button.click()
    assert menu_button.get_attribute("aria-expanded") == "true"
    page.wait_for_timeout(250)
    assert page.evaluate("document.activeElement.closest('#app-nav') !== null")
    shot(page, "phase1", "menu-open-phone")

    page.keyboard.press("Escape")
    assert menu_button.get_attribute("aria-expanded") == "false"
    assert page.evaluate("document.activeElement.getAttribute('aria-controls')") == "app-nav"


def test_every_menu_link_is_reachable_on_a_short_landscape_phone(game, browser, live_server):
    from .conftest import session_cookie

    context = browser.new_context(viewport={"width": 667, "height": 375}, has_touch=True)
    context.add_cookies([session_cookie(game["admin"], live_server)])
    page = context.new_page()
    page.goto(live_server.url + reverse("core:dashboard"))
    page.get_by_role("button", name="Toggle navigation").click()
    page.wait_for_timeout(250)

    users_link = page.locator("#app-nav").get_by_role("link", name="Users")
    users_link.scroll_into_view_if_needed()
    box = users_link.bounding_box()
    footer = page.locator("#app-nav > div").last.bounding_box()
    assert box["y"] + box["height"] <= footer["y"] + 1, "the profile card covers the last links"
    context.close()


@pytest.mark.parametrize(
    ("page_name", "label"),
    [("seasons", "New season"), ("users", "Invite"), ("managers", "New profile")],
)
def test_the_main_action_is_there_on_a_phone(game, open_page, page_name, label):
    _, user, path = next(p for p in key_pages(game) if p[0] == page_name)
    page = open_page(user, path)

    action = page.get_by_role("button", name=label).or_(page.get_by_role("link", name=label))
    assert action.first.is_visible()
    box = action.first.bounding_box()
    assert box["width"] >= 40 and box["height"] >= 40


def test_a_detail_page_has_a_back_arrow_on_a_phone(game, open_page):
    page = open_page(game["admin"], reverse("accounts:user-detail", args=[game["user"].pk]))

    assert page.get_by_role("link", name="Back to team").first.is_visible()


def test_fields_do_not_make_ios_zoom(game, open_page):
    page = open_page(game["user"], reverse("nba:player-list"))

    sizes = page.evaluate(
        "[...document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]), select')]"
        ".filter(el => el.offsetParent).map(el => parseFloat(getComputedStyle(el).fontSize))"
    )
    assert sizes and min(sizes) >= 16


def test_a_dialog_keeps_its_buttons_on_screen(game, browser, live_server):
    from .conftest import session_cookie

    context = browser.new_context(viewport={"width": 375, "height": 667}, has_touch=True)
    context.add_cookies([session_cookie(game["user"], live_server)])
    page = context.new_page()
    page.goto(live_server.url + reverse("fantasy:manager-list"))
    page.get_by_role("button", name="New profile").click()
    page.wait_for_selector(".modal-panel")
    page.wait_for_timeout(300)

    actions = page.locator(".modal-actions").first.bounding_box()
    assert actions["y"] + actions["height"] <= 667 + 1
    shot(page, "phase1", "dialog-sheet-phone")
    context.close()


def test_header_and_menu_controls_are_big_enough_to_tap(game, open_page):
    page = open_page(game["user"], reverse("fantasy:roster-build", args=[game["roster"].pk]))

    small = page.evaluate(SMALL_TARGETS_JS, "header button, header a.btn, header [role=button]")
    assert not small, f"header controls under 40px: {small}"


def test_report_remaining_small_targets(game, open_page):
    """Not a gate: lists what Phases 2 and 3 still have to enlarge."""
    print("\nTouch targets under 40px at 375px (outside header and menu):")
    for name, user, path in key_pages(game):
        page = open_page(user, path)
        small = page.evaluate(SMALL_TARGETS_JS, "main button, main a.btn, main [role=button]")
        for target in small[:6]:
            print(f"  {name:<10} {target['width']:>3}x{target['height']:<3} {target['text']}")
        if len(small) > 6:
            print(f"  {name:<10} … {len(small) - 6} more")
