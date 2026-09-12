"""Form plumbing shared across the apps.

Here rather than in `accounts` because it is about how a form looks, not about
who is signing in. `accounts` had it first and was the only app with forms;
`fantasy` grew one for the season editor, and a second copy of the same
Tailwind string is how two screens start to drift apart.
"""

from django import forms

# Every field in the app gets these classes, so the widgets stay consistent
# without repeating Tailwind strings in each template.
INPUT_CLASSES = (
    "block w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm "
    "text-tertiary-700 placeholder-neutral-400 shadow-sm transition "
    "focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/30 "
    "disabled:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 "
    "dark:text-neutral-100 dark:placeholder-neutral-500"
)

CHECKBOX_CLASSES = "h-4 w-4 rounded border-neutral-300 text-primary-500 focus:ring-primary-500/30"


class StyledFormMixin:
    """Applies the shared input styling to every visible widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", CHECKBOX_CLASSES)
            else:
                widget.attrs.setdefault("class", INPUT_CLASSES)
            if field.required:
                widget.attrs.setdefault("required", "required")
