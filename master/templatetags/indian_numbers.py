from django import template

register = template.Library()


@register.filter
def indian(value):
    """1234567 -> 12,34,567: digit grouping as written in India."""
    try:
        digits = str(abs(int(value)))
    except (TypeError, ValueError):
        return "–"
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ("-" if int(value) < 0 else "") + ",".join(groups + [tail])
