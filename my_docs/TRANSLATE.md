# English and German Translation Plan

## Purpose

Add a complete English/German interface to the Django application while keeping the current English experience stable, preserving user-entered and NBA data, and allowing each authenticated user to keep a language preference.

This document is a plan only. It does not define an implementation already completed beyond the foundation described below.

## Current Foundation

The persistent language preference is already in place:

- `apps.accounts.User.language` stores `en` or `de`.
- The profile form exposes the setting.
- `LocaleMiddleware` and `apps.accounts.middleware.UserLanguageMiddleware` activate the saved language for authenticated requests.
- `LANGUAGES` and `LOCALE_PATHS` are configured in `config/settings/base.py`.
- A migration and profile-flow tests cover saving and reusing the preference.

The translation layer is not complete yet:

- Templates do not currently use `{% translate %}` or `{% blocktranslate %}`.
- No `locale/de/LC_MESSAGES/django.po` catalog exists yet.
- Most visible English copy is hard-coded in templates.
- Some Python model and form labels already use `gettext_lazy`, but view messages and other user-facing strings need review.

Phase 0 is complete once the locale defaults and catalog toolchain are verified. The
language preference foundation above is part of the same translation rollout.

## Goals

- Translate all user-facing static copy into English and German.
- Keep English as the default language.
- Make the saved profile preference apply on subsequent authenticated requests.
- Translate form labels, validation errors, flash messages, empty states, navigation, actions, help text, and email content.
- Preserve NBA team names, player names, statistics, roster names, manager names, and other user/API data.
- Keep URLs, permission checks, business rules, HTMX behavior, and JavaScript behavior language-neutral.
- Make translation changes reviewable and testable.

## Non-Goals

- Do not translate user-entered content automatically.
- Do not translate NBA proper nouns unless a product decision explicitly requires it.
- Do not introduce localized URL paths in the first version.
- Do not translate the Django admin unless it is part of a separate staff localization decision.
- Do not rely on machine translation without a human review pass.

## Phase 0: Normalize and Prepare (Completed)

### Work

1. Change `LANGUAGE_CODE` from `en-us` to `en` so it matches the stored user preference and configured language codes.
2. Confirm `LANGUAGES` contains exactly the supported product languages:
   - `en` - English
   - `de` - German
3. Confirm `LOCALE_PATHS` points to the repository-level `locale` directory.
4. Verify that `LocaleMiddleware` remains after `SessionMiddleware` and that the user-language middleware runs after `AuthenticationMiddleware`.
5. Install or document the system gettext tools required by Django's `makemessages` and `compilemessages` commands. On macOS, install GNU gettext with `brew install gettext`; on Debian-based build images, install the `gettext` package.
6. Decide on the product terminology before translating repeated concepts such as roster, watchlist, manager, season, trade, and staff area.

### Exit criteria

- `python manage.py check` passes.
- The default request language is `en`.
- Existing profile preference tests still pass.
- The team has agreed on a small English/German terminology glossary.

The current repository uses `en` and `de` consistently. The Phase 1 catalog commands
require the gettext executables to be available in the local shell or build image.

## Phase 1: Create the Translation Catalog Workflow (Completed)

### Work

1. Add `{% load i18n %}` to templates that contain translation tags. The first tagged slice covers the shared shell, common status controls, login, and profile templates; feature templates continue in later phases.
2. Mark static template text with `{% translate %}`.
3. Mark text containing variables, conditionals, or HTML structure with `{% blocktranslate %}` where appropriate.
4. Mark Python strings with `gettext`, `gettext_lazy`, or `_` according to when the string is evaluated.
5. Generate the German catalog after each tagged slice. The first catalog contains the shared shell, account templates, and existing account-side Python messages:

   ```bash
   uv run python manage.py makemessages -l de
   ```

6. Review the generated `locale/de/LC_MESSAGES/django.po` entries for accidental extraction of:
   - CSS classes
   - URLs and route names
   - NBA/API values
   - user-entered content
   - developer-only comments
7. Add `locale/` to version control and compile catalogs for local and deployment use:

   ```bash
   uv run python manage.py compilemessages
   ```

8. Add smoke tests around one shared-shell string and one account string so the
   catalog workflow is proven before the remaining feature areas are translated.

### Translation rules

- Use stable source strings; do not use English text as a database value or program control flag.
- Keep HTML out of translations where possible.
- Use `blocktranslate` with named variables instead of string concatenation.
- Use plural-aware `blocktranslate` or `ngettext` for counts.
- Keep punctuation and capitalization consistent across languages.
- Use German umlauts and normal German typography in the catalog; source files remain UTF-8.
- Do not translate values that come from the database or an external API.

### Exit criteria

- Catalog generation completes without extraction errors.
- The German catalog compiles successfully.
- A small smoke test renders one English and one German translated string.

## Phase 2: Complete the Shared Application Shell

### Scope

Complete the remaining shared components after the Phase 1 foundation:

- `templates/base.html`
- `templates/app.html`
- `templates/auth_base.html`
- `templates/partials/messages.html`
- `templates/partials/form_field.html`
- `templates/partials/modal.html`
- `templates/partials/modal_confirm.html`
- `templates/partials/modal_form.html`
- `templates/partials/modal_prompt.html`
- `templates/partials/theme_toggle.html`

### Copy to cover

- Main navigation and staff navigation
- Profile and sign-out actions
- Mobile navigation labels and accessibility labels
- Loading and status text
- Modal actions such as cancel, confirm, close, save, and delete
- Common form errors and success messages
- Page titles and shared document titles

### Exit criteria

- The authenticated shell is usable in both languages.
- No English-only navigation or shared action remains in the shell.
- Mobile and HTMX-rendered partials use the active language.
- Accessibility labels and status messages are translated as well as visible text.

## Phase 3: Complete Accounts and Authentication

### Scope

- `templates/accounts/profile.html`
- Password change and reset templates
- Invitation and invitation acceptance templates
- User list and user detail templates
- `apps/accounts/forms.py`
- `apps/accounts/views.py`
- Account email subjects and bodies

### Work

- Translate the remaining account templates, form labels, help text, validation errors, and authentication messages. Login and profile have initial coverage from Phase 1 and should be used as the pattern for the remaining workflows.
- Add the language selector to the profile form using translated language names only where appropriate; keep `English` and `Deutsch` recognizable in both locales.
- Decide whether unauthenticated pages should follow the browser language or remain English by default. The recommended behavior is browser/session locale for anonymous users and saved preference for authenticated users.
- Verify invitation and password-reset emails use the recipient or request language consistently. If that is deferred, document the temporary English-only behavior.

### Exit criteria

- A user can sign in, change language, sign out, and sign back in without losing the preference.
- All account workflows have translated labels, errors, confirmations, and empty states.
- Password-reset and invitation flows have an explicit language behavior.

## Phase 4: Translate Core and Dashboard Pages

### Scope

- `templates/core/dashboard.html`
- Core view-generated messages and page titles
- Empty states, summary labels, and dashboard actions

### Work

- Translate dashboard sections and next actions.
- Use pluralization for counts.
- Keep dates and numbers locale-aware where Django formatting is appropriate.
- Verify that translated text does not change layout or cause overflow in the existing responsive shell.

### Exit criteria

- The dashboard contains no unintended English copy when German is active.
- Date and count formatting is correct for both locales.
- Existing dashboard tests continue to pass.

## Phase 5: Translate NBA Features

### Scope

- Team list and team detail pages
- Player list, detail, comparison, and statistics pages
- Watchlist controls
- Injury status and refresh partials
- Sorting headers, chart labels, legends, and explanatory text

### Work

- Translate interface labels and descriptions, not team/player/API names.
- Review chart configuration carefully; strings rendered by JavaScript may need to be passed from translated template context or exposed through a small translation data object.
- Translate status values only if they are application-owned. Keep external API values unchanged unless they are normalized first.
- Check HTMX responses independently; partial responses must activate the same language as full-page responses.

### Exit criteria

- Full-page and HTMX NBA workflows render consistently in both languages.
- Charts, tooltips, labels, and accessibility text are covered.
- API and database data remain unchanged.

## Phase 6: Translate Fantasy Features

### Scope

- Rosters and roster creation
- Roster history and rules
- Manager profiles
- Trades and trade modals
- Seasons and season administration
- Fantasy success/error messages and service-level validation text

### Work

- Translate all actions, constraints, confirmations, empty states, and transaction messages.
- Use plural-aware strings for roster/player counts and transaction summaries.
- Keep roster names, manager names, nicknames, and transaction data user-owned and untranslated.
- Review modal and HTMX flows on narrow screens because German labels may be longer.

### Exit criteria

- Core fantasy workflows are complete in both languages.
- Business-rule errors are translated at the source where they are created.
- No translated string causes an action to become ambiguous or unusable.

## Phase 7: Tests and Quality Gates

### Automated tests

Add or extend tests for:

- English default rendering.
- German rendering with `translation.override("de")`.
- Saved German preference being applied on later authenticated requests.
- Browser/session language behavior for anonymous users.
- Translated profile labels and navigation.
- Form validation errors in both languages.
- Flash messages in both languages.
- HTMX partial responses in both languages.
- Plural forms and count-dependent messages.
- Catalog compilation and migration consistency.

### Manual checks

For each supported language, review:

- Desktop and mobile navigation.
- Profile and authentication flows.
- Every modal and confirmation action.
- Empty, loading, success, and error states.
- Long German labels in buttons, tables, headings, and cards.
- Screen-reader labels and live regions.
- Dates, numbers, currency, and pluralization.
- Dark mode and responsive layouts.

### Quality gates

Run at minimum:

```bash
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py compilemessages
uv run pytest -q
```

The translation work should not be considered complete while catalog compilation, full tests, or a feature-area language review is failing.

## Phase 8: Deployment and Maintenance

### Deployment

1. Commit the `.po` source catalogs and generated `.mo` files if the deployment process expects compiled catalogs in the repository.
2. Run the user-language migration in every environment.
3. Ensure the release image has gettext runtime support if catalogs are compiled during deployment.
4. Verify the production build includes `locale/` and compiled message files.
5. Smoke-test English and German after deployment using a real account for each preference.

### Ongoing workflow

For every new user-facing string:

1. Mark it for translation when it is introduced.
2. Regenerate the message catalog.
3. Add or review the German translation.
4. Compile the catalog.
5. Add a focused test when the string affects behavior, permissions, counts, or a critical workflow.
6. Include translation review in pull requests that change visible copy.

## Recommended Delivery Order

Deliver the work in these reviewable slices:

1. Normalize locale defaults and establish catalog tooling.
2. Translate shared shell and common partials.
3. Translate accounts and authentication.
4. Translate dashboard and core pages.
5. Translate NBA pages and chart/HTMX strings.
6. Translate fantasy pages and business-rule messages.
7. Complete automated and manual quality checks.
8. Deploy catalogs and document the maintenance workflow.

This order makes the language switch visibly useful early, limits the size of each review, and keeps missing translations easy to locate.
