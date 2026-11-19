from uuid import uuid4

from django import template

register = template.Library()


@register.simple_tag
def feedback_submission_id() -> str:
    return str(uuid4())
