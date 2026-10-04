# Mobile experience – implementation plan

Status: planned, not started · Branch: `feature/mobile` (on top of `feature/ux-round-3`)
Estimated effort: 4–5 working days · Delivery: one commit per phase, each checked on staging

## 1. Goal

The app should work as well on a phone as on a desktop:

- Every action is reachable on a phone.
- Nothing the user needs is hidden behind hover.
- The page itself never scrolls sideways.
- The key flows (browsing players, building a roster, trading, notes) are comfortable with one thumb.

The brand stays the same: calm, minimal, professional, light and dark mode.

## 2. Decisions

| Topic | Decision |
|---|---|
| Navigation | Fix the existing side menu properly. No bottom tab bar for now. |
| Roster squad on phones | A card list with Trade/Release on each card and a "Sort by" dropdown. The table stays from `md`. |
| Comparison on phones | A transposed table: one row per figure, one column per player, figure column fixed. |
| Trade dialog on phones | Two steps (player out → player in) with a fixed summary and "Execute trade" at the bottom. Desktop unchanged. |
| Players list on phones | Search always visible. The other filters behind "Filters (n)", the chart behind "Show chart". |
| Dashboard countdown | One row of 4 tiles with scaling digits, a smaller hero, and a reload at zero. |
| Verification | Playwright as a development-only dependency, plus template tests. |
| Delivery | Branch `feature/mobile`, one commit per phase. |

## 3. Principles for every change

- **Widths:** designed for **375px** (iPhone SE/mini). Checked at 390, 768 and 1024px. Only tables may scroll sideways, and only inside their own frame (`overflow-x-auto`), never the page.
- **Touch targets:** at least **40×40px**, and 44px for the main actions. Small icons can keep their look and get the tap area through padding.
- **No information that appears only on hover.** `title` is an extra and never the only carrier of information.
- **Form fields on phones are at least 16px** (`text-base sm:text-sm`), so iOS does not zoom in on focus.
- **Safe areas:** fixed elements at the bottom use `env(safe-area-inset-bottom)`, and the viewport meta gets `viewport-fit=cover`.
- **Dynamic viewport height:** `dvh` instead of `vh` for anything full-height, because the iOS address bar changes the height.
- **Motion:** only the existing gentle transitions, with `motion-safe:` / `prefers-reduced-motion` respected.
- **Breakpoints:** Tailwind's own `sm` 640, `md` 768 and `lg` 1024. "Phone" means below `sm`, unless a task says otherwise.
- **Translations:** every new text in English and German, using the established terms (Cash, Kader, Transferphase, Verpflichten, Watchlist).

## 4. Current state

Measured from the templates in October 2026.

| Area | Finding | Location |
|---|---|---|
| Side menu | Reachable with the Tab key when closed. No `aria-expanded`/`aria-controls`. Escape does not close it. Does not scroll. The profile card (`absolute bottom-0`) covers the bottom links on short screens. | `templates/app.html:6-140` |
| Header buttons | Hidden below `sm` with no alternative: "New season", "Invite", "New profile", "Back to team", and the back link on the manager page. | `season_list.html:15`, `user_list.html:8`, `manager_list.html:12`, `user_detail.html:8`, `manager_detail.html:8` |
| Form fields | `text-sm` (14px), so iOS zooms in on every focus. | `apps/core/forms.py:14` (`INPUT_CLASSES`), plus search and select fields in the templates |
| Tables | Roster squad `min-w-[75rem]`. History `min-w-[44rem]`. Players table 13 columns. Comparison 12 columns. No table keeps its name column in view. | `roster_panel.html:221`, `roster_history.html:116`, `player_table.html:37`, `player_compare.html:88` |
| Touch targets | Several icon buttons are 20–32px: info popover, remove-from-compare, watchlist (32px), theme switch, injury and others. | `partials/info_popover.html`, `player_compare.html`, `watchlist_button.html`, `theme_toggle.html`, `injury_status.html` |
| Hover-only | Sort arrows appear only on hover. | `nba/partials/sort_header.html:23` |
| Tooltips | 64 `title` attributes. Some carry needed information, such as why "Sign" is disabled in the market ("Full", "Too dear", "Closed"). | `player_picker.html`, `roster_panel.html`, and others |
| Dialogs | They open from the bottom on phones (good), but have no height limit and no fixed action bar. In the trade dialog the two lists stack and "Execute trade" sits far down. | `assets/input.css:285-310`, `trade_body.html` |
| Players list | 6 filter fields stack on phones and fill the first screen. The scatter chart comes before the table. | `player_list.html:36`, `player_table.html:17-31` |
| Dashboard | The countdown tiles stack with `text-7xl` digits and push the buttons out of view. The countdown stops at zero without reloading. | `core/dashboard.html:29-47` |

---

## Phase 0 – Setup for visual checks (about 0.5 days, before Phase 1)

**Goal:** see the screens at phone width before and after each phase.

### Tasks
1. **Dev dependencies:** `uv add --dev pytest-playwright`, then install Chromium with `uv run playwright install chromium`.
   - Neither ends up in the production image: the Dockerfile only installs main dependencies. This must be checked against the Dockerfile.
   - `README.md` documents the one-off browser install.
2. **Separate test marker:** `@pytest.mark.browser`. Browser tests do not run in the normal `uv run pytest` (`addopts = "-m 'not browser'"`) but explicitly with `uv run pytest -m browser`.
3. **Fixture `live_app`:** `pytest-django`'s `live_server` plus seed data, so every page has something to show:
   - 30 teams (already there through the migration);
   - about 40 players with salary, FP and snapshots, so Hotness and Value appear;
   - a current season with a transfer phase and trade grants;
   - a user with a manager profile, a half-full roster and notes;
   - a staff user and an admin.
4. **Helper `screens(page, name)`:** saves screenshots to `tmp/screens/<phase>/<name>-<width>-<theme>.png`, at **375×812** and **768×1024**, in light and dark mode. The folder is ignored by git.
5. **Baseline screenshots** of the key pages before any change:
   - Home, players list, player page, comparison, team list and team page;
   - My rosters, build page, trade dialog, history, rules;
   - seasons, users and the user page.

### Automatic checks (part of every later phase)
- **No sideways page scroll:** `document.documentElement.scrollWidth <= window.innerWidth` on every key page at 375px.
- **Main action visible:** each page's main action is inside the first viewport at 375px.

### Done when
`uv run pytest -m browser` runs locally, produces the baseline screenshots, and lists the pages that scroll sideways today.

---

## Phase 1 – Foundation: shell, navigation, global rules (about 1 day)

**Commit:** "Mobile foundation: navigation, header actions, inputs, touch targets, sheets"

### 1.1 Side menu (`templates/app.html`)
- **Accessibility:**
  - The `aside` gets `id="app-nav"`. The menu button gets `aria-controls="app-nav"` and `:aria-expanded="navOpen"`.
  - Below `lg` and while closed: `x-bind:inert="!navOpen && !isDesktop"`. `isDesktop` comes from `matchMedia('(min-width: 1024px)')`, kept current via `change` events.
- **Keyboard:**
  - Escape closes the menu (`@keydown.escape.window`).
  - On open, focus moves to the first link. On close, it returns to the menu button (`$refs.menuButton`).
- **No background scroll:** `document.body.classList.toggle('overflow-hidden', navOpen)` below `lg`.
- **Layout:** the `aside` becomes `flex flex-col h-dvh`.
  - The top part stays as it is.
  - The link list is `flex-1 overflow-y-auto`.
  - The profile card moves from `absolute bottom-0` into normal flow, with `pb-[env(safe-area-inset-bottom)]`.
- **Closing:** tapping a link closes the menu (`@click` on the list when `navOpen`).
- **Acceptance:**
  - In a 390×600 landscape view all links are reachable.
  - With the menu closed, the Tab key never lands in the menu.
  - Escape closes it.

### 1.2 Header actions on phones
- **New snippet `templates/partials/header_action.html`**, with context `href` or `hx_get`, `icon` (name), `label`, `variant` (primary/secondary/ghost):
  - below `sm`: an icon button `h-10 w-10` with `aria-label`;
  - from `sm`: icon + word, as today.
  - The SVG icons sit in a small icon snippet `partials/icon.html` with a `name` parameter (plus, back, trade, history, rules, …), so they are not copied.
- **Used for:**
  - "New season" (`season_list.html`), "Invite" (`user_list.html`), "New profile" (`manager_list.html`);
  - the existing Trade/History/Rules in `roster_build.html`, so they share one pattern.
- **Back links** (`user_detail.html`, `manager_detail.html`): a new block `{% block back_link %}` in `app.html`. Below `sm` it is a back arrow left of the page title (`h-10 w-10`, `aria-label="Back to …"`). From `sm` it is the button as today.
- **Acceptance:** every page offers the same actions at 375px as at 1024px.

### 1.3 Form fields
- `apps/core/forms.py` `INPUT_CLASSES`: `text-sm` → `text-base sm:text-sm`.
- Templates with their own field classes (search in `player_list.html`, `player_picker.html`, `trade_body.html`, `user_list.html`, `compare`, the filter selects): the same change. A template test (1.8) finds the spots.
- **Acceptance:** no field below 16px at 375px, so no zoom on focus in iOS Safari.

### 1.4 Touch targets
- **Icon buttons below `sm`:** at least `h-10 w-10`, today's size from `sm`. This applies to:
  - watchlist (`watchlist_button.html`), injury (`injury_status.html`), remove-from-compare (`player_compare.html`);
  - note Edit/Delete (`note.html`), theme switch (`theme_toggle.html`), roster icons in the list (`manager_list.html`).
- **Info popover:** the visible icon stays 14px, and the tap area becomes 40px through `p-3 -m-3` (negative margin only horizontal, in line with the template rule against negative vertical margins). Alternatively `h-10 w-10` on the button with a centred icon.
- **Sort arrows:** always faintly visible (`opacity-30` instead of `opacity-0`), full strength on the active column.
- **Acceptance:** a Playwright check measures `getBoundingClientRect()` of all `button, a.btn, [role=button]` at 375px and reports anything under 40×40, with an allow-list for inline text links.

### 1.5 Tooltips that carry information
- **Inventory** of all 64 `title` attributes in a table, as part of the commit description: keep it, make it visible, or replace it with an info popover.
- **Make visible** (short text next to or under the control):
  - the "Sign" disabled reasons in the market ("Full", "Too dear", "Closed"), as `player_picker.html` shows as button text;
  - the trade buy/sell reasons in the roster panel, as a line under the buttons;
  - the "Short" badge on the roster list, with the reason under it;
  - the injury refresh limit.
- **Info popover** for explanations that are too long for the layout.
- **Keep as an extra:** exact amounts on money figures (`title="$12,345,678"`) and position names on position badges.

### 1.6 Dialogs as bottom sheets on phones (`assets/input.css`, `templates/partials/modal.html`)
- **`.modal-panel` below `sm`:**
  - full width with no side margin (`-mx-4` on the backdrop's padding), `rounded-t-2xl rounded-b-none`;
  - `max-h-[90dvh]`, `flex flex-col`;
  - the content area `overflow-y-auto`.
- **`.modal-actions` below `sm`:**
  - `sticky bottom-0` inside the panel, with a background and a top border;
  - `pb-[max(1rem,env(safe-area-inset-bottom))]`;
  - buttons full width (already the case).
- **From `sm`:** unchanged, a centred dialog.
- A small grab bar at the top of the sheet (decoration only, `aria-hidden`).
- **Acceptance:** for every dialog (confirmation, form, prompt, trade, import, role, notes deletion) the action buttons stay visible without scrolling at 375×667.

### 1.7 Viewport and safe areas
- `base.html`: `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">`.
- Toast area (`app.html`): `bottom-[max(1rem,env(safe-area-inset-bottom))]` below `sm`.
- Loading overlay: `h-dvh` instead of `inset-0` with `vh`, if it applies.

### 1.8 Tests for Phase 1
Template tests in `tests/test_responsive_chrome.py`:
- every `hidden sm:inline-flex` element in `header_actions` has a visible alternative below `sm` (via the `header_action.html` snippet, or a marker the test recognises);
- `INPUT_CLASSES` contains `text-base`, and no `<input>`/`<select>`/`<textarea>` in the templates has `text-sm` without `text-base`;
- disabled buttons have no `title` as their only reason (they carry `aria-describedby` or visible text).

Plus a test for the menu attributes (`aria-controls`, `aria-expanded`, `inert`), the browser checks from Phase 0 at 375px, and screenshots before/after in `tmp/screens/phase1/`.

---

## Phase 2 – Tables on small screens (about 1.5 days)

**Commit:** "Mobile tables: sticky names, fewer columns, squad and history as cards, transposed compare"

### 2.1 Players table (`nba/partials/player_table.html`, also used by the team page)
- **Fixed first columns:** the watchlist and name columns get `sticky left-0` / `left-[3rem]` with `bg-white dark:bg-neutral-900`, plus a soft right edge (`shadow-[inset_-1px_0]`) so the scrolling content does not show through.
- **Hidden below `md`:** "Expected salary" and "Points" (`hidden md:table-cell` on `th` and `td`).
- **"Status" column:** only shown when inactive players are included (`request.GET.inactive`); otherwise it always says "Active".
- **Column visibility in one place:** a dict in the view, so `th` and `td` stay in step.
- **Paging:** "Previous/Next" buttons at touch size; the page counter wraps below them on phones.

### 2.2 Roster squad (`fantasy/partials/roster_panel.html`)
- **New snippet `fantasy/partials/squad_cards.html`**, shown only below `md` (`md:hidden`); the table gets `hidden md:block`. One card per player:
  - **Row 1:** position badge, name (link), team abbreviation, injury badge.
  - **Row 2, small figures:** salary, Value (snippet), FP/G, Hotness (snippet).
  - **Row 3, actions:** "Trade" (opens the trade dialog with `?out=`) and "Release" (or "Signings closed"), full width split 50/50, `h-11`.
- **"Sort by" dropdown** above the cards with the same keys as the table (`name`, `salary`, `difference`, `avg`, `hotness`, …). It writes `?sort=&dir=` and reloads the panel via HTMX.
- **Data:** the same `memberships` list as the table, so no extra queries.
- **Acceptance:** at 375px every action of a squad player is reachable without sideways scrolling.

### 2.3 Roster history (`fantasy/roster_history.html`)
- Below `md`, a card list instead of the 44rem table: date/time, type badge, "out → in" with prices, cash change (signed), balance.
- The summary at the top (starting budget, counts, reconciliation) stays as it is.
- Grant rows (`GRANT_TRADE`) appear as "Helpside Trade · +1 trade".

### 2.4 Comparison page (`nba/player_compare.html`)
- **Below `md` a transposed table:**
  - rows: Team, position, salary, Expected, Value, Points, FP/G, games, Hotness, injury, status;
  - columns: the selected players (at most 5);
  - the first column (figure name) is `sticky left-0`;
  - the column headers are player names (links) with a "remove" button at touch size.
- **From `md`:** the existing table, unchanged.
- **Implementation:** the view prepares a row list (`compare_rows`: label, key, values per player), so the template does not repeat the logic. Both layouts read from it.
- The "Add player" button is full width on phones.

### 2.5 Other tables
- **Seasons (`season_list.html`):**
  - below `sm`, hide "Snapshots" and "State" (State becomes a badge next to the label);
  - action icons at 40px.
- **Users (`accounts/partials/user_table.html`):** below `sm`, show "Role" as a small line under the name instead of its own column; the "Action" column stays.
- **Team list (`nba/team_list.html`):** below `sm`, hide "Salary"; "Injured" becomes a small badge under the name when > 0.
- **Snapshots on the player page (`player_detail.html:248`):**
  - below `md`, only date, salary, FP/G and games;
  - the changes are shown as small arrows instead of their own columns.

### 2.6 Tests for Phase 2
- Template tests check that the card snippets exist, that each `th` with `hidden md:table-cell` has a matching `td`, and that sticky cells have a background.
- View tests check that `compare_rows` has the right order and values.
- Browser checks: no page scroll at 375px on the players list, team page, build page, history and comparison; the squad cards show "Trade"/"Release" in the viewport; screenshots in `tmp/screens/phase2/`.

---

## Phase 3 – Key flows on phones (about 1.5 days)

**Commit:** "Mobile flows: player filters, build page, two-step trade, dashboard, player page"

### 3.1 Players list (`nba/player_list.html`)
- **Search** stays visible at full width.
- **The other filters** (position, team, injury status, max salary, min games, roster, watchlist, inactive) sit in an Alpine disclosure below `sm`:
  - button **"Filters"** with a counter badge for active filters (counted in the view: `active_filter_count`);
  - expands in place;
  - "Reset filters" link inside.
  - From `sm`: always open as today (`sm:block`).
- **Scatter chart** behind **"Show chart"** below `md`, open from `md`.
  - It stays outside the HTMX-swapped part, so opening it survives a filter change. To do that the chart moves out of `#player-table` into its own block that is updated out-of-band (`hx-swap-oob`).
- **Acceptance:** at 375px the first player row is visible without scrolling.

### 3.2 Build page (`fantasy/roster_build.html`, `roster_panel.html`, `player_picker.html`)
- **Stats band** in the roster panel: on phones 2 columns (as today), with the "Available Trades" cell spanning the full width and the Buy/Sell buttons at `h-10`.
- **Bar above the market** on phones:
  - line 1: positions (G 3/5 · F 4/5 · C 1/2) and cash;
  - line 2, only while slots are open: "Avg. per open slot";
  - height at most 2 lines.
- **Market rows:**
  - "Sign" at touch size;
  - disabled reasons as visible short text (see 1.5);
  - salary and FP/G in one line under the name, so the row needs no width.
- **Header:** Trade/History/Rules/Delete already use icons; they switch to the `header_action.html` snippet (1.2).

### 3.3 Trade dialog in two steps (`fantasy/trade_modal.html`, `partials/trade_body.html`)
- **Below `sm`, a step flow controlled by the server's state:**
  - **Step 1, "Who goes out?":** the squad list. Choosing a player loads the body with `?out=…&step=2` (already possible via `keep_*`).
  - **Step 2, "Who comes in?":** search/position filter, list with affordability and the "leaves 4 G" badges; "← Change player out" goes back to step 1.
  - **Fixed summary** at the bottom (inside `modal-actions`): "Out → In", cash afterwards (red if negative), visible reason line, button **"Execute trade"**.
- **`?out=` already present** (e.g. coming from a squad card): start directly in step 2.
- **`?in=` present** (e.g. coming from the player page): step 1 shows the chosen incoming player at the top as a note.
- **From `sm`:** the two-column layout unchanged.
- **Implementation:** a context variable `mobile_step` (1/2) from the parameters. The template renders both lists, but below `sm` hides the inactive step (`max-sm:hidden` on the inactive section). No JavaScript state; the server stays the source of truth.

### 3.4 Dashboard (`core/dashboard.html`)
- **Countdown:**
  - `grid-cols-4` at every width;
  - digits via `text-[clamp(2rem,11vw,10rem)]`, label line below in `text-[10px]`;
  - `aria-live="off"` on the digits (no screen-reader flood) and one `sr-only` text with the target time.
- **Hero on phones:** less padding (`p-6` instead of `p-10`), and the grid texture and pulsing dot only from `sm` (`motion-safe:`). The buttons ("My roster", "Players") are visible without scrolling at 375×667.
- **At zero:** one `location.reload()`. A guard via `sessionStorage` prevents a loop if the next target is also in the past.
- The "two h1" issue: the hero title becomes `h2`, while the shell's `h1` stays.

### 3.5 Player page (`nba/player_detail.html`)
- **Header on phones:**
  - name + watchlist on line 1;
  - position · team on line 2;
  - buttons (injury, Compare, All players) as a scrollable or wrapping row of compact buttons (`h-10`), with "All players" as a back arrow left of the title (block `back_link`, see 1.2).
- **"Your rosters" box** above the stat cards on phones (`order-first` in a flex column), below them from `md`, because signing/trading is the action on this page.
- **Stat cards:** 2 per row on phones (as today); the info popovers open fully on screen (`right-0`/`left-0` depending on the column, so the popover is not cut off at the edge).

### 3.6 Tests for Phase 3
- View tests for `active_filter_count`, `mobile_step` (1 without `out`, 2 with `out`), and that `?in=` + no `out` shows the hint in step 1.
- Template tests: filter disclosure present, chart toggle present, trade summary in `modal-actions`.
- Browser checks at 375px:
  - players list: the first row is in the viewport after load;
  - trade dialog: step 1 → choose → step 2 → "Execute trade" visible without scrolling;
  - dashboard: buttons in the viewport;
  - player page: the "Your rosters" box before the stat cards.
- Screenshots in `tmp/screens/phase3/`.

---

## 5. Manual check on staging (after each phase)

On a real iPhone (Safari) and Android phone (Chrome), portrait and landscape:

- [ ] Menu opens and closes, Escape (with a keyboard) closes it, all links reachable in landscape.
- [ ] Every page shows its main action (New season, Invite, New profile, Back).
- [ ] Tapping into search, filter and note fields does not zoom the page.
- [ ] Players list: filters fold out, chart folds out, the first player visible straight away.
- [ ] Build page: sign a player, see the toast, release from the squad card.
- [ ] Trade: step 1 → step 2 → execute; the summary always visible.
- [ ] Every dialog: buttons visible, not hidden behind the home indicator.
- [ ] Notes: add, edit, delete.
- [ ] Comparison: transposed table readable, players removable.
- [ ] Dark mode on all of the above.

## 6. Risks and how to handle them

| Risk | Handling |
|---|---|
| Duplicate markup (table + cards) drifts apart | Both read from the same data (`memberships`, `compare_rows`). Template tests check that every column has a card field. |
| Chart outside the HTMX swap breaks the filter update | Out-of-band update (`hx-swap-oob`) with a test for the response. |
| `inert` in older browsers | Safari ≥ 15.5 and Chrome ≥ 102 support it. Without it, only the Tab key behaves as today; nothing breaks. |
| Playwright makes the CI slower | Browser tests are marked `browser` and are not part of the normal run. |
| German texts are longer and break layouts | Screenshots in `de` as well (user language set in the fixture). |

## 7. Out of scope for this round

- Bottom tab bar (decision: no for now)
- Offline/PWA, push notifications
- Redesigning the scatter chart (colours, legend, fair-value curve), which is its own topic
- Injury badge as text instead of an icon, which is its own topic
