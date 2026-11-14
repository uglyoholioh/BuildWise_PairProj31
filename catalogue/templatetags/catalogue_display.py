from django import template

from catalogue.models import specification_fields


register = template.Library()


@register.filter
def specification_items(specs):
    # A JSON key named "items" shadows dict.items in Django's dot lookup.
    return specification_fields(specs).items()
