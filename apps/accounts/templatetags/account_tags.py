from django import template

from apps.accounts.services import build_invitation_url

register = template.Library()


@register.simple_tag(takes_context=True)
def invitation_url(context, user):
    request = context.get("request")
    if not request:
        return ""
    return build_invitation_url(user, request)
