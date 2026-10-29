from django.db.models import Sum

from catalogue.models import Component
from inventory.models import Reservation


def purchased_pc_allocation_issues(purchased_pc):
    line = purchased_pc.order_line
    if line.custom_snapshot.get("components"):
        expected = {
            part["component_id"]: part["quantity"]
            for part in line.custom_snapshot.get("components", [])
            if "component_id" in part and "quantity" in part
        }
    elif line.configuration_id:
        expected = dict(line.configuration.items.values_list("component_id", "quantity"))
    else:
        expected = {}
    if not expected:
        return ["No component allocation details are recorded for this PC."]

    components = Component.objects.in_bulk(expected)
    actual = dict(
        Reservation.objects.filter(
            purchased_pc=purchased_pc,
            status=Reservation.Status.RESERVED,
            component__isnull=False,
        )
        .values("component_id")
        .annotate(total=Sum("quantity"))
        .values_list("component_id", "total")
    )
    problems = []
    for component_id, quantity in expected.items():
        component = components.get(component_id)
        label = str(component) if component else f"Component {component_id}"
        if component and not component.active:
            problems.append(f"{label} is inactive.")
        if actual.get(component_id, 0) < quantity:
            problems.append(f"{label} is not fully reserved.")
    return problems


def accessory_allocation_issues(order):
    problems = []
    for line in order.lines.filter(kind="accessory").select_related("accessory"):
        reserved = Reservation.objects.filter(
            order_line=line,
            status=Reservation.Status.RESERVED,
            accessory=line.accessory,
        ).aggregate(total=Sum("quantity"))["total"] or 0
        if reserved < line.quantity:
            problems.append(f"{line.name} is not fully reserved.")
    return problems
