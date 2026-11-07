from django.db.models import Sum

from catalogue.compatibility import check_configuration_components
from catalogue.models import Component
from inventory.availability import available_component_quantity
from inventory.models import Reservation


def component_snapshot(component, quantity=1):
    return {
        "component_id": component.pk, "quantity": quantity,
        "category": component.category, "brand": component.brand, "model": component.model,
        "price": str(component.price), "socket": component.socket,
        "memory_type": component.memory_type, "form_factor": component.form_factor,
        "ml_tier": component.ml_tier, "specs": component.specs,
    }


def approved_pc_parts(purchased_pc, proposals=None):
    """Replay approvals without changing the purchase snapshot.

    Batch readers can supply accepted proposals in decision order to avoid repeated queries.
    """
    line = purchased_pc.order_line
    parts = [dict(part) for part in line.custom_snapshot.get("components", [])]
    if not parts and line.configuration_id:
        parts = [
            component_snapshot(item.component, item.quantity)
            for item in line.configuration.items.select_related("component")
        ]
    if proposals is None:
        proposals = purchased_pc.substitutions.filter(status="accepted").order_by("decided_at", "pk")
    for proposal in proposals:
        original = None
        for part in parts:
            if part["component_id"] == proposal.original_component_id and part["quantity"] > 0:
                original = part
                break
        if original:
            original["quantity"] -= 1
            replacement = dict(proposal.replacement_snapshot or component_snapshot(proposal.replacement_component))
            replacement["quantity"] = 1
            parts.append(replacement)
    return [part for part in parts if part["quantity"] > 0]


def expected_pc_quantities(purchased_pc):
    expected = {}
    for part in approved_pc_parts(purchased_pc):
        component_id = part["component_id"]
        expected[component_id] = expected.get(component_id, 0) + part["quantity"]
    return expected


def reserved_pc_quantities(purchased_pc):
    return dict(
        Reservation.objects.filter(
            purchased_pc=purchased_pc,
            status=Reservation.Status.RESERVED,
            component__isnull=False,
        )
        .values("component_id")
        .annotate(total=Sum("quantity"))
        .values_list("component_id", "total")
    )


def substitution_issues(purchased_pc, original, replacement):
    """Validate a one-unit change against the complete approved specification."""
    expected = expected_pc_quantities(purchased_pc)
    if expected.get(original.pk, 0) < 1:
        return ["The original component is no longer part of this PC."]
    problems = []
    if original.pk == replacement.pk or original.category != replacement.category:
        problems.append("Choose a different component in the same category.")
    actual = reserved_pc_quantities(purchased_pc)
    # The replaced unit may be missing after QC removed a faulty unit.
    for component_id, quantity in expected.items():
        minimum = quantity - 1 if component_id == original.pk else quantity
        if actual.get(component_id, 0) < minimum:
            problems.append("Other required parts are not fully reserved.")
    if any(quantity > expected.get(component_id, 0) for component_id, quantity in actual.items()):
        problems.append("The current allocations do not match this PC's approved specification.")
    proposed = expected.copy()
    proposed[original.pk] -= 1
    proposed[replacement.pk] = proposed.get(replacement.pk, 0) + 1
    ids = [component_id for component_id, quantity in proposed.items() if quantity > 0]
    components = Component.objects.in_bulk(ids)
    if len(components) != len(ids):
        problems.append("A required component no longer exists.")
    problems.extend(check_configuration_components(components.values()))
    if available_component_quantity(replacement) < 1:
        problems.append("The proposed component is no longer available.")
    return problems


def purchased_pc_allocation_issues(purchased_pc):
    expected = expected_pc_quantities(purchased_pc)
    if not expected:
        return ["No component allocation details are recorded for this PC."]

    components = Component.objects.in_bulk(expected)
    actual = reserved_pc_quantities(purchased_pc)
    problems = []
    for component_id, quantity in expected.items():
        component = components.get(component_id)
        label = str(component) if component else f"Component {component_id}"
        if component and not component.active:
            problems.append(f"{label} is inactive.")
        if actual.get(component_id, 0) != quantity:
            problems.append(f"{label} is not fully reserved.")
    if any(component_id not in expected for component_id in actual):
        problems.append("Unexpected components are reserved for this PC.")
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
