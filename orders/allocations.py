from django.db.models import Sum

from catalogue.compatibility import check_configuration_components
from catalogue.models import Component, ConfigurationItem
from inventory.availability import available_component_quantity
from inventory.models import Reservation


def component_snapshot(component, quantity=1):
    return {
        "component_id": component.pk, "quantity": quantity,
        "category": component.category, "brand": component.brand, "model": component.model,
        "price": str(component.price), "socket": component.socket,
        "memory_type": component.memory_type, "form_factor": component.form_factor,
        "ml_tier": component.ml_tier, "specs": component.specs,
        "requires_cpu_cooler": component.requires_cpu_cooler,
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


def replace_pc_part(parts, original_id, replacement):
    after = [dict(part) for part in parts]
    for part in after:
        if part["component_id"] == original_id:
            original = {**part, "quantity": 1}
            part["quantity"] -= 1
            after.append({**replacement, "quantity": 1})
            return original, [part for part in after if part["quantity"] > 0]
    return {}, after


def snapshot_items(parts):
    """Build unsaved ORM objects for the existing predictor, without catalogue reads."""
    items = []
    for part in parts:
        component = Component(
            pk=part["component_id"],
            category=part.get("category", ""),
            brand=part.get("brand", ""),
            model=part.get("model", ""),
            socket=part.get("socket", ""),
            memory_type=part.get("memory_type", ""),
            form_factor=part.get("form_factor", ""),
            ml_tier=part.get("ml_tier", ""),
            specs=part.get("specs", {}),
            requires_cpu_cooler=part.get("requires_cpu_cooler", False),
        )
        items.append(ConfigurationItem(component=component, quantity=part["quantity"]))
    return items


def expected_pc_quantities(purchased_pc):
    return _part_quantities(approved_pc_parts(purchased_pc))


def _part_quantities(parts):
    expected = {}
    for part in parts:
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
    parts = approved_pc_parts(purchased_pc)
    original_snapshot, proposed_parts = replace_pc_part(parts, original.pk, component_snapshot(replacement))
    if not original_snapshot:
        return ["The original component is no longer part of this PC."]
    problems = []
    if original.pk == replacement.pk or original_snapshot.get("category", original.category) != replacement.category:
        problems.append("Choose a different component in the same category.")
    if not replacement.active:
        problems.append("The proposed component is no longer available for sale.")
    expected = _part_quantities(parts)
    actual = reserved_pc_quantities(purchased_pc)
    # The replaced unit may be missing after QC removed a faulty unit.
    for component_id, quantity in expected.items():
        minimum = quantity - 1 if component_id == original.pk else quantity
        if actual.get(component_id, 0) < minimum:
            problems.append("Other required parts are not fully reserved.")
    if any(quantity > expected.get(component_id, 0) for component_id, quantity in actual.items()):
        problems.append("The current allocations do not match this PC's approved specification.")
    components = [item.component for item in snapshot_items(proposed_parts)]
    problems.extend(check_configuration_components(components, require_active=False))
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
        if actual.get(component_id, 0) != quantity:
            problems.append(f"{label} is not fully reserved.")
    if any(component_id not in expected for component_id in actual):
        problems.append("Unexpected components are reserved for this PC.")
    return problems


def reserve_restocked_part(purchased_pc):
    expected = expected_pc_quantities(purchased_pc)
    actual = reserved_pc_quantities(purchased_pc)
    if any(quantity > expected.get(component_id, 0) for component_id, quantity in actual.items()):
        return None
    missing = {component_id: quantity - actual.get(component_id, 0) for component_id, quantity in expected.items()}
    shortfalls = [component_id for component_id, quantity in missing.items() if quantity]
    if len(shortfalls) != 1 or missing[shortfalls[0]] != 1:
        return None
    part = Component.objects.filter(pk=shortfalls[0]).first()
    if part is None or available_component_quantity(part) < 1:
        return None
    Reservation.objects.create(purchased_pc=purchased_pc, component=part, quantity=1)
    return part


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
