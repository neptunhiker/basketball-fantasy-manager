"""Small helpers for HTMX responses."""

import json

TOAST_HEADER = "HX-Trigger-After-Swap"


def add_toast(response, message, level="success"):
    """Show `message` as a toast once HTMX has swapped `response` in.

    Sent as a `toast` event in `HX-Trigger-After-Swap` rather than `HX-Trigger`:
    the element that made the request is often replaced by the swap (a Sign
    button in a re-rendered list), and HTMX then fires after-swap events on the
    body instead, where the toast area in `app.html` still hears them. It also
    leaves `HX-Trigger` free for `close-modal`.

    `level` is "success" (fades after a few seconds) or "error" (stays until
    dismissed). Returns the response, so it can wrap a `return`.
    """
    try:
        triggers = json.loads(response.get(TOAST_HEADER) or "{}")
    except json.JSONDecodeError:
        triggers = {}
    triggers["toast"] = {"message": str(message), "level": level}
    response[TOAST_HEADER] = json.dumps(triggers)
    return response
