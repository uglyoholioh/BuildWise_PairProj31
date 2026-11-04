from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import OperationalError, transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import Customer, StaffAction
from catalogue.compatibility import check_configuration_components
from catalogue.models import Accessory, AssemblyFeeSetting, Component, Configuration, ConfigurationItem
from catalogue.pricing import calculate_pc_price
from inventory.availability import available_accessory_quantity, available_component_quantity
from inventory.models import Reservation, StockAdjustment
from .allocations import (
    accessory_allocation_issues,
    component_snapshot,
    expected_pc_quantities,
    purchased_pc_allocation_issues,
    reserved_pc_quantities,
    substitution_issues,
)
from .forms import CheckoutForm, DispatchForm, HoldPCForm, QualityCheckForm, SubstitutionProposalForm
from .models import Order, OrderLine, OrderStatusHistory, PurchasedPC, QualityCheck, Shipment, SubstitutionProposal
from .substitutions import replacement_details_changed, substitution_review_snapshot


def _build_snapshot(items):
    return {"components": [component_snapshot(item.component, item.quantity) for item in items]}


def _cart_build(row):
    if row.get("kind") == "preset":
        configuration = Configuration.objects.filter(
            pk=row.get("configuration_id"), status=Configuration.Status.PUBLISHED
        ).prefetch_related("items__component").first()
        if not configuration:
            return None, [], ["This preset is no longer available."]
        items = list(configuration.items.all())
        name = configuration.name
    else:
        snapshot = row.get("custom_snapshot", {})
        ids = [part.get("component_id") for part in snapshot.get("components", [])]
        components = Component.objects.in_bulk(ids)
        items = [
            ConfigurationItem(component=components[part["component_id"]], quantity=part["quantity"])
            for part in snapshot.get("components", [])
            if part.get("component_id") in components and part.get("quantity", 0) > 0
        ]
        configuration = None
        name = row.get("name", "Custom PC")
    issues = check_configuration_components(item.component for item in items)
    return configuration, items, issues


@require_http_methods(["GET", "POST"])
def cart(request):
    lines = request.session.get("cart", [])
    if request.method == "POST":
        line_id = request.POST.get("line_id")
        action = request.POST.get("action")
        for line in lines:
            if str(line.get("id")) != line_id:
                continue
            if action == "remove":
                lines.remove(line)
            elif action == "update":
                try:
                    quantity = int(request.POST.get("quantity", ""))
                except ValueError:
                    quantity = 0
                if 1 <= quantity <= 20:
                    line["quantity"] = quantity
                else:
                    messages.error(request, "Quantity must be between 1 and 20.")
            break
        request.session["cart"] = lines
        return redirect("orders:cart")
    fee = AssemblyFeeSetting.objects.order_by("-updated_at", "-pk").first()
    display_lines = []
    total = 0
    for line in lines:
        accessory = None
        if line.get("kind") == "accessory":
            accessory = Accessory.objects.filter(pk=line.get("accessory_id"), active=True).first()
            items, issues = [], [] if accessory else ["This accessory is no longer available."]
            price = accessory.price if accessory else None
            stock = available_accessory_quantity(accessory) if accessory else 0
        else:
            configuration, items, issues = _cart_build(line)
            price = calculate_pc_price(items, fee.fee_amount) if fee and not issues else None
            stock = min((available_component_quantity(item.component) // item.quantity for item in items), default=0) if items else 0
        quantity = line.get("quantity", 1)
        old_price = line.get("quoted_price")
        changed = price is not None and old_price is not None and str(price) != str(old_price)
        line["price_changed"] = line.get("price_changed", False) or changed
        line["quoted_price"] = str(price) if price is not None else None
        display_lines.append({"line": line, "items": items, "accessory": accessory, "issues": issues, "price": price, "changed": changed, "stock": stock})
        if price is not None:
            total += price * quantity
    request.session["cart"] = lines
    return render(request, "orders/cart.html", {"lines": display_lines, "total": total, "has_cart": bool(lines)})


@require_POST
def cart_add(request):
    kind = request.POST.get("kind")
    if kind == "preset":
        configuration = get_object_or_404(
            Configuration.objects.prefetch_related("items__component"),
            pk=request.POST.get("item_id"), status=Configuration.Status.PUBLISHED,
        )
        items = list(configuration.items.all())
        name = configuration.name
        configuration_id = configuration.pk
    elif kind == "custom":
        build = request.session.get("current_build", {})
        ids = [part.get("component_id") for part in build.get("components", [])]
        components = Component.objects.in_bulk(ids)
        items = [ConfigurationItem(component=components[part["component_id"]], quantity=part["quantity"]) for part in build.get("components", []) if part.get("component_id") in components]
        name, configuration_id = build.get("name", "Custom PC"), None
    elif kind == "accessory":
        accessory = get_object_or_404(Accessory, pk=request.POST.get("item_id"), active=True)
        if not available_accessory_quantity(accessory):
            messages.error(request, "This accessory is out of stock.")
            return redirect("catalogue:accessory_list")
        lines = request.session.get("cart", [])
        existing = next((line for line in lines if line.get("kind") == "accessory" and line.get("accessory_id") == accessory.pk), None)
        if existing:
            existing["quantity"] = min(20, existing.get("quantity", 1) + 1)
        else:
            import uuid
            lines.append({"id": uuid.uuid4().hex, "key": f"accessory:{accessory.pk}", "kind": "accessory", "accessory_id": accessory.pk, "name": accessory.name, "quantity": 1})
        request.session["cart"] = lines
        messages.success(request, f"{accessory.name} added to your cart.")
        return redirect("orders:cart")
    else:
        messages.error(request, "Choose a valid build to add to the cart.")
        return redirect("orders:cart")
    issues = check_configuration_components(item.component for item in items)
    available = min((available_component_quantity(item.component) // item.quantity for item in items), default=0)
    fee = AssemblyFeeSetting.objects.order_by("-updated_at", "-pk").first()
    if issues or not items or not available or not fee:
        messages.error(request, "This build is incomplete, unavailable, incompatible, or missing a PC assembly fee.")
        return redirect("orders:cart")
    quantity = 1
    snapshot = _build_snapshot(items)
    source_preset = configuration if kind == "preset" else Configuration.objects.filter(
        pk=build.get("preset_id"),
    ).first()
    if source_preset:
        snapshot.update(source_preset_id=source_preset.pk, source_preset_name=source_preset.name,
                        customised_from_preset=kind == "custom")
    key = f"preset:{configuration_id}" if configuration_id else "custom:" + ",".join(
        f"{part['component_id']}x{part['quantity']}" for part in sorted(snapshot["components"], key=lambda value: value["component_id"])
    )
    if kind == "custom":
        key += f":source:{source_preset.pk if source_preset else 'none'}"
    lines = request.session.get("cart", [])
    existing = next((line for line in lines if line.get("key") == key), None)
    if existing:
        existing["quantity"] = min(20, existing.get("quantity", 1) + quantity)
    else:
        import uuid
        lines.append({"id": uuid.uuid4().hex, "key": key, "kind": kind, "configuration_id": configuration_id, "name": name, "custom_snapshot": snapshot, "quantity": quantity})
    request.session["cart"] = lines
    messages.success(request, f"{name} added to your cart.")
    return redirect("orders:cart")


@login_required
@require_http_methods(["GET", "POST"])
def checkout(request):
    customer = get_object_or_404(Customer, user=request.user)
    lines = request.session.get("cart", [])
    if not lines:
        messages.error(request, "Your cart is empty.")
        return redirect("orders:cart")
    fee = AssemblyFeeSetting.objects.order_by("-updated_at", "-pk").first()
    form = CheckoutForm(request.POST or None, instance=customer)
    preview = []
    total = 0
    for line in lines:
        accessory = None
        if line.get("kind") == "accessory":
            accessory = Accessory.objects.filter(pk=line.get("accessory_id"), active=True).first()
            items, issues = [], [] if accessory else ["An accessory in your cart is no longer available."]
            price = accessory.price if accessory else None
        else:
            configuration, items, issues = _cart_build(line)
            price = calculate_pc_price(items, fee.fee_amount) if fee and not issues else None
        if price is not None:
            total += price * line.get("quantity", 1)
        price_changed = price is not None and line.get("quoted_price") not in (None, str(price))
        line["price_changed"] = line.get("price_changed", False) or price_changed
        preview.append({"line": line, "items": items, "accessory": accessory, "configuration": configuration if line.get("kind") != "accessory" else None, "issues": issues, "price": price, "changed": price_changed or line["price_changed"]})
    invalid = any(row["issues"] or row["price"] is None or (row["line"].get("kind") != "accessory" and not fee) for row in preview)
    if request.method == "POST" and form.is_valid() and not invalid:
        if any(row["changed"] for row in preview) and not form.cleaned_data["confirm_prices"]:
            for row in preview:
                if row["price"] is not None:
                    row["line"]["quoted_price"] = str(row["price"])
                    row["line"]["price_changed"] = True
            request.session["cart"] = lines
            messages.warning(request, "Prices changed. Review the updated total and confirm before placing your order.")
            return redirect("orders:cart")
        try:
            with transaction.atomic():
                # Cart line IDs are generated by the server and survive simultaneous submits.
                checkout_reference = lines[0].get("id")
                existing_order = None
                if checkout_reference:
                    existing_order = Order.objects.filter(
                        customer=customer, checkout_reference=checkout_reference,
                    ).first()
                if existing_order:
                    request.session["cart"] = []
                    request.session.pop("current_build", None)
                    return redirect("orders:order_detail", pk=existing_order.pk)
                component_ids = sorted({item.component_id for row in preview for item in row["items"]})
                locked = {item.pk: item for item in Component.objects.select_for_update().filter(pk__in=component_ids, active=True)}
                if len(locked) != len(component_ids):
                    raise ValueError("A component in your cart is no longer available.")
                accessory_ids = sorted({row["accessory"].pk for row in preview if row["accessory"]})
                locked_accessories = {item.pk: item for item in Accessory.objects.select_for_update().filter(pk__in=accessory_ids, active=True)}
                if len(locked_accessories) != len(accessory_ids):
                    raise ValueError("An accessory in your cart is no longer available.")
                requirements = {"component": {}, "accessory": {}}
                for row in preview:
                    if row["issues"]:
                        raise ValueError("An item in your cart is no longer available or compatible.")
                    line = row["line"]
                    if line.get("kind") == "accessory":
                        accessory = locked_accessories[row["accessory"].pk]
                        current_price = accessory.price
                        requirements["accessory"][accessory.pk] = requirements["accessory"].get(accessory.pk, 0) + line.get("quantity", 1)
                        row["accessory"] = accessory
                    else:
                        for item in row["items"]:
                            item.component = locked[item.component_id]
                            requirements["component"][item.component_id] = requirements["component"].get(item.component_id, 0) + item.quantity * line.get("quantity", 1)
                        current_issues = check_configuration_components(item.component for item in row["items"])
                        if current_issues:
                            raise ValueError("A build in your cart is no longer compatible.")
                        current_price = calculate_pc_price(row["items"], fee.fee_amount)
                    if str(current_price) != str(row["price"]):
                        row["line"]["quoted_price"] = str(current_price)
                        row["line"]["price_changed"] = True
                        request.session["cart"] = lines
                        raise ValueError("Prices changed during checkout. Review the updated cart and confirm again.")
                    row["price"] = current_price
                total = sum(row["price"] * row["line"].get("quantity", 1) for row in preview)
                for component_id, quantity in requirements["component"].items():
                    if available_component_quantity(locked[component_id]) < quantity:
                        raise ValueError(f"Not enough stock for {locked[component_id]}.")
                for accessory_id, quantity in requirements["accessory"].items():
                    if available_accessory_quantity(locked_accessories[accessory_id]) < quantity:
                        raise ValueError(f"Not enough stock for {locked_accessories[accessory_id]}.")
                customer.name = form.cleaned_data["name"]
                customer.delivery_address = form.cleaned_data["delivery_address"]
                customer.save(update_fields=("name", "delivery_address"))
                order = Order.objects.create(
                    checkout_reference=checkout_reference,
                    customer=customer,
                    payment_status="paid",
                    total_snapshot=total,
                    delivery_name=customer.name,
                    delivery_address=customer.delivery_address,
                )
                OrderStatusHistory.objects.create(order=order, to_status="placed", note="Simulated payment succeeded and stock was reserved.")
                for row in preview:
                    line = row["line"]
                    is_accessory = line.get("kind") == "accessory"
                    if is_accessory:
                        snapshot = {"accessory": {
                            "name": row["accessory"].name,
                            "category": row["accessory"].category,
                        }}
                    else:
                        snapshot = _build_snapshot(row["items"])
                        for field in ("source_preset_id", "source_preset_name", "customised_from_preset"):
                            if field in line.get("custom_snapshot", {}):
                                snapshot[field] = line["custom_snapshot"][field]
                    order_line = OrderLine.objects.create(
                        order=order, kind="accessory" if is_accessory else "build",
                        name=row["accessory"].name if is_accessory else line["name"],
                        quantity=line.get("quantity", 1), unit_price_snapshot=row["price"],
                        configuration=row.get("configuration"), accessory=row.get("accessory"),
                        custom_snapshot=snapshot,
                    )
                    if line.get("kind") == "accessory":
                        Reservation.objects.create(accessory=row["accessory"], order_line=order_line, quantity=order_line.quantity)
                    else:
                        for unit_index in range(1, order_line.quantity + 1):
                            pc = PurchasedPC.objects.create(order_line=order_line, unit_index=unit_index)
                            for item in row["items"]:
                                Reservation.objects.create(component=locked[item.component_id], purchased_pc=pc, quantity=item.quantity)
        except OperationalError:
            messages.error(request, "Inventory is being updated. Your cart is unchanged; please try checkout again.")
            return redirect("orders:cart")
        except ValueError as error:
            messages.error(request, str(error))
            return redirect("orders:cart")
        request.session["cart"] = []
        request.session.pop("current_build", None)
        messages.success(request, "Payment simulated successfully. Your order has been placed.")
        return redirect("orders:order_detail", pk=order.pk)
    return render(request, "orders/checkout.html", {"form": form, "lines": preview, "total": total, "invalid": invalid})


@login_required
def order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.select_related("shipment").prefetch_related(
            "lines__pcs__reservations__component",
            "lines__pcs__substitutions",
            "lines__pcs__review",
            "lines__pcs__substitutions__original_component",
            "lines__pcs__substitutions__replacement_component",
            "lines__pcs__quality_checks__employee",
            "status_history",
        ),
        pk=pk,
        customer__user=request.user,
    )
    can_decide_substitutions = (
        order.payment_status == "paid"
        and order.status not in ("cancelled", "delivered", "dispatched", "packing")
    )
    return render(request, "orders/order_detail.html", {
        "order": order, "can_decide_substitutions": can_decide_substitutions,
    })


@login_required
def order_list(request):
    orders = Order.objects.filter(customer__user=request.user).select_related("shipment").prefetch_related("status_history").order_by("-placed_at", "-pk")
    return render(request, "orders/order_list.html", {"orders": orders})


@require_http_methods(["GET", "POST"])
def propose_substitution(request, pk):
    order_id = _order_id_for_pc(pk)
    pc = get_object_or_404(PurchasedPC.objects.select_related("order_line__order"), pk=pk)
    order = pc.order_line.order
    form = SubstitutionProposalForm(
        request.POST if request.method == "POST" else None, purchased_pc=pc
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            order = get_object_or_404(Order.objects.select_for_update(), pk=order_id)
            pc = get_object_or_404(PurchasedPC.objects.select_for_update(), pk=pk)
            if (
                order.payment_status != "paid"
                or order.status in ("cancelled", "delivered", "dispatched", "packing")
                or pc.status not in ("queued", "assembling", "quality_checking", "corrective_work", "on_hold")
                or pc.substitutions.filter(status="pending").exists()
            ):
                form.add_error(None, "This PC cannot receive another substitution proposal.")
            else:
                original = get_object_or_404(Component, pk=form.cleaned_data["original_component"].pk)
                replacement = get_object_or_404(
                    Component.objects.select_for_update(), pk=form.cleaned_data["replacement_component"].pk
                )
                problems = substitution_issues(pc, original, replacement)
                if problems:
                    form.add_error("replacement_component", " ".join(problems))
                else:
                    original_snapshot, replacement_snapshot, performance_snapshot = substitution_review_snapshot(
                        pc, original, replacement,
                    )
                    SubstitutionProposal.objects.create(
                        purchased_pc=pc,
                        original_component=original,
                        replacement_component=replacement,
                        proposed_by=request.user.employee_profile,
                        reason=form.cleaned_data["reason"],
                        spec_differences=form.cleaned_data["spec_differences"],
                        fps_note=form.cleaned_data["fps_note"],
                        previous_pc_status=pc.status,
                        original_snapshot=original_snapshot,
                        replacement_snapshot=replacement_snapshot,
                        performance_snapshot=performance_snapshot,
                    )
                    pc.status = "on_hold"
                    pc.hold_reason = "Waiting for customer approval of a component substitution."
                    pc.save(update_fields=("status", "hold_reason"))
                    StaffAction.objects.create(
                        employee=request.user.employee_profile,
                        action_type="pc_substitution_proposed",
                        object_ref=str(pc.pk),
                        detail=f"Proposed {replacement} in place of {original} for PC #{pc.pk}.",
                    )
                    messages.success(request, "Substitution proposal sent to the customer.")
                    return redirect("orders:order_queue")
    return render(request, "orders/substitution_form.html", {"form": form, "pc": pc, "order": order})


@login_required
@require_POST
def decide_substitution(request, pk):
    with transaction.atomic():
        proposal = get_object_or_404(
            SubstitutionProposal.objects.select_related("purchased_pc__order_line__order"), pk=pk
        )
        order_id = proposal.purchased_pc.order_line.order_id
        order = get_object_or_404(
            Order.objects.select_for_update(), pk=order_id, customer__user=request.user
        )
        pc = get_object_or_404(PurchasedPC.objects.select_for_update(), pk=proposal.purchased_pc_id)
        proposal = get_object_or_404(SubstitutionProposal.objects.select_for_update(), pk=pk)
        decision = request.POST.get("decision")
        if (
            proposal.status != "pending"
            or pc.status != "on_hold"
            or order.payment_status != "paid"
            or order.status in ("cancelled", "delivered", "dispatched", "packing")
        ):
            messages.error(request, "This substitution is no longer awaiting a decision.")
        elif decision == "accept":
            replacement = get_object_or_404(
                Component.objects.select_for_update(), pk=proposal.replacement_component_id
            )
            problems = substitution_issues(pc, proposal.original_component, replacement)
            if problems:
                pc.hold_reason = " ".join(problems)
                pc.save(update_fields=("hold_reason",))
                messages.error(request, f"The substitution cannot be accepted yet: {pc.hold_reason}")
            elif replacement_details_changed(proposal, replacement):
                # Retain the old offer and require a decision on a new proposal ID.
                original_snapshot, replacement_snapshot, performance_snapshot = substitution_review_snapshot(
                    pc, proposal.original_component, replacement,
                )
                SubstitutionProposal.objects.create(
                    purchased_pc=pc,
                    original_component=proposal.original_component,
                    replacement_component=replacement,
                    proposed_by=proposal.proposed_by,
                    reason=proposal.reason,
                    previous_pc_status=proposal.previous_pc_status,
                    original_snapshot=original_snapshot,
                    replacement_snapshot=replacement_snapshot,
                    performance_snapshot=performance_snapshot,
                )
                proposal.status = "superseded"
                proposal.save(update_fields=("status",))
                pc.hold_reason = "Replacement details changed. Please review the updated proposal before deciding."
                pc.save(update_fields=("hold_reason",))
                OrderStatusHistory.objects.create(
                    order=order, from_status=order.status, to_status=order.status,
                    note=f"PC #{pc.pk}: proposal #{proposal.pk} refreshed after replacement specifications changed; customer approval is still required.",
                )
                messages.warning(request, pc.hold_reason)
            else:
                expected = expected_pc_quantities(pc)
                actual = reserved_pc_quantities(pc)
                # If QC already removed the faulty unit, there is nothing to release.
                if actual.get(proposal.original_component_id, 0) >= expected[proposal.original_component_id]:
                    reservation = Reservation.objects.filter(
                        purchased_pc=pc, component_id=proposal.original_component_id,
                        status=Reservation.Status.RESERVED,
                    ).order_by("pk").first()
                    if reservation.quantity == 1:
                        reservation.status = Reservation.Status.RELEASED
                        reservation.save(update_fields=("status",))
                    else:
                        reservation.quantity -= 1
                        reservation.save(update_fields=("quantity",))
                Reservation.objects.create(component=replacement, purchased_pc=pc, quantity=1)
                proposal.status = "accepted"
                proposal.decided_at = timezone.now()
                proposal.decided_by = f"customer:{request.user.pk}"
                proposal.save(update_fields=("status", "decided_at", "decided_by"))
                pc.status = proposal.previous_pc_status
                if pc.status == "on_hold":
                    if pc.quality_checks.exists():
                        pc.status = "corrective_work"
                    elif order.status == "placed":
                        pc.status = "queued"
                    else:
                        pc.status = "assembling"
                pc.hold_reason = ""
                pc.save(update_fields=("status", "hold_reason"))
                OrderStatusHistory.objects.create(
                    order=order, from_status=order.status, to_status=order.status,
                    note=f"Customer accepted substitution #{proposal.pk} for PC #{pc.pk}; one replacement unit reserved and the confirmed price unchanged.",
                )
                messages.success(request, "Substitution accepted. The confirmed order price is unchanged.")
        elif decision == "decline":
            for reservation in Reservation.objects.select_for_update().filter(
                purchased_pc=pc, status=Reservation.Status.RESERVED, component__isnull=False
            ):
                if proposal.previous_pc_status == "queued":
                    reservation.status = Reservation.Status.RELEASED
                    reservation.save(update_fields=("status",))
            proposal.status = "declined"
            proposal.decided_at = timezone.now()
            proposal.decided_by = f"customer:{request.user.pk}"
            proposal.save(update_fields=("status", "decided_at", "decided_by"))
            pc.status = "cancelled"
            pc.refund_amount = pc.order_line.unit_price_snapshot
            pc.hold_reason = "Cancelled after declining the proposed substitution."
            pc.save(update_fields=("status", "refund_amount", "hold_reason"))
            previous_order_status = order.status
            remaining_pcs = PurchasedPC.objects.filter(order_line__order=order).exclude(status="cancelled")
            has_accessories = order.lines.filter(kind="accessory").exists()
            if not remaining_pcs.exists() and not has_accessories:
                order.status = "cancelled"
                order.payment_status = "refunded"
                order.save(update_fields=("status", "payment_status"))
            OrderStatusHistory.objects.create(
                order=order, from_status=previous_order_status, to_status=order.status,
                note=f"PC #{pc.pk} cancelled after substitution was declined; full PC-price refund recorded.",
            )
            messages.success(request, "This PC was cancelled and its full PC price was recorded for refund.")
        else:
            messages.error(request, "Choose accept or decline.")
    return redirect("orders:order_detail", pk=order_id)


def order_queue(request):
    orders = list(
        Order.objects.filter(payment_status="paid")
        .exclude(status__in=("cancelled", "delivered"))
        .select_related("customer")
        .prefetch_related(
            "lines__pcs__reservations__component",
            "lines__pcs__reservations__accessory",
            "lines__configuration__items__component",
            "lines__accessory",
            "lines__reservations__component",
            "lines__reservations__accessory",
        )
        .order_by("placed_at", "pk")
    )
    for order in orders:
        lines = list(order.lines.all())
        order.is_accessory_only = bool(lines) and all(line.kind == "accessory" for line in lines)
        order.accessory_allocation_issues = accessory_allocation_issues(order)
        for line in lines:
            for purchased_pc in line.pcs.all():
                purchased_pc.allocation_issues = (
                    [] if purchased_pc.status == "quality_passed"
                    else purchased_pc_allocation_issues(purchased_pc)
                )
                purchased_pc.has_pending_substitution = purchased_pc.substitutions.filter(status="pending").exists()
        pcs = [pc for line in lines for pc in line.pcs.all()]
        order.pcs_ready = (
            all(line.kind != "build" or line.pcs.count() == line.quantity for line in lines)
            and all(pc.status in ("quality_passed", "cancelled") for pc in pcs)
        )
        order.can_start_packing = (
            order.payment_status == "paid"
            and order.status in ("placed", "in_progress")
            and bool(lines)
            and order.pcs_ready
            and not accessory_allocation_issues(order)
        )
        order.can_dispatch = order.payment_status == "paid" and order.status == "packing" and order.pcs_ready and not order.accessory_allocation_issues
    return render(request, "orders/order_queue.html", {"orders": orders})


def _order_id_for_pc(pk):
    order_id = PurchasedPC.objects.filter(pk=pk).values_list("order_line__order_id", flat=True).first()
    if order_id is None:
        raise Http404
    return order_id


@login_required
@require_POST
def order_cancel(request, pk):
    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update(), pk=pk, customer__user=request.user
        )
        if order.status != "placed" or order.payment_status != "paid":
            messages.error(request, "This order can no longer be cancelled.")
        else:
            reservations = Reservation.objects.select_for_update().filter(
                Q(order_line__order=order) | Q(purchased_pc__order_line__order=order),
                status=Reservation.Status.RESERVED,
            )
            for reservation in reservations:
                reservation.status = Reservation.Status.RELEASED
                reservation.save(update_fields=("status",))
            for pc in PurchasedPC.objects.select_for_update().filter(order_line__order=order):
                pc.status = "cancelled"
                pc.refund_amount = pc.order_line.unit_price_snapshot
                pc.save(update_fields=("status", "refund_amount"))
            order.status = "cancelled"
            order.payment_status = "refunded"
            order.save(update_fields=("status", "payment_status"))
            OrderStatusHistory.objects.create(
                order=order,
                from_status="placed",
                to_status="cancelled",
                note=f"Customer cancelled before assembly or packing started; simulated refund of ${order.total_snapshot} recorded and reservations released.",
            )
            messages.success(request, "Your order has been cancelled.")
    return redirect("orders:order_list")


@require_POST
def start_order(request, pk):
    order_id = _order_id_for_pc(pk)
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=order_id)
        pc = get_object_or_404(
            PurchasedPC.objects.select_for_update().select_related("order_line"), pk=pk
        )
        if order.payment_status != "paid" or order.status in ("cancelled", "delivered"):
            messages.error(request, "This order cannot be started.")
        elif pc.substitutions.filter(status="pending").exists():
            messages.error(request, "This PC is waiting for customer approval.")
        elif pc.status not in ("queued", "on_hold"):
            messages.error(request, "This PC is no longer waiting to start.")
        else:
            issues = purchased_pc_allocation_issues(pc)
            if issues:
                pc.status = "on_hold"
                pc.hold_reason = " ".join(issues)
                pc.save(update_fields=("status", "hold_reason"))
                StaffAction.objects.create(
                    employee=request.user.employee_profile,
                    action_type="pc_allocation_hold",
                    object_ref=str(pc.pk),
                    detail=pc.hold_reason,
                )
                messages.error(request, f"PC #{pc.pk} was put on hold: {pc.hold_reason}")
            else:
                pc.status = "assembling"
                pc.hold_reason = ""
                pc.save(update_fields=("status", "hold_reason"))
                if order.status == "placed":
                    order.status = "in_progress"
                    order.save(update_fields=("status",))
                    OrderStatusHistory.objects.create(
                        order=order,
                        from_status="placed",
                        to_status="in_progress",
                        note=f"Assembly started for PC #{pc.pk}.",
                        employee=request.user.employee_profile,
                    )
                StaffAction.objects.create(
                    employee=request.user.employee_profile,
                    action_type="pc_assembly_started",
                    object_ref=str(pc.pk),
                    detail=f"Started assembly for PC #{pc.pk} in order #{order.pk}.",
                )
                messages.success(request, f"Assembly started for PC #{pc.pk}.")
    return redirect("orders:order_queue")


@require_POST
def start_quality_check(request, pk):
    order_id = _order_id_for_pc(pk)
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=order_id)
        pc = get_object_or_404(PurchasedPC.objects.select_for_update(), pk=pk)
        if order.status in ("cancelled", "delivered") or pc.status != "assembling" or pc.substitutions.filter(status="pending").exists():
            messages.error(request, "This PC is not ready for quality checks.")
        else:
            pc.status = "quality_checking"
            pc.save(update_fields=("status",))
            StaffAction.objects.create(
                employee=request.user.employee_profile,
                action_type="pc_quality_check_started",
                object_ref=str(pc.pk),
                detail=f"Started quality checks for PC #{pc.pk}.",
            )
            messages.success(request, f"Quality checks started for PC #{pc.pk}.")
    return redirect("orders:order_queue")


@require_POST
def record_quality_check(request, pk):
    order_id = _order_id_for_pc(pk)
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=order_id)
        pc = get_object_or_404(PurchasedPC.objects.select_for_update(), pk=pk)
        form = QualityCheckForm(request.POST, purchased_pc=pc)
        if not form.is_valid():
            messages.error(request, "Choose a check, and include a reason when it fails.")
        elif pc.substitutions.filter(status="pending").exists() or purchased_pc_allocation_issues(pc):
            messages.error(request, "Resolve pending approval and missing allocations before quality checks.")
        elif order.status in ("cancelled", "delivered") or pc.status not in (
            "quality_checking", "corrective_work"
        ):
            messages.error(request, "This PC is not accepting quality checks.")
        else:
            passed = form.cleaned_data["passed"] == "yes"
            note = form.cleaned_data["note"].strip()
            check = QualityCheck.objects.create(
                purchased_pc=pc,
                employee=request.user.employee_profile,
                stage="quality_checking",
                checklist_item=form.cleaned_data["checklist_item"],
                passed=passed,
                note=note,
            )
            faulty = form.cleaned_data["faulty_component"]
            if not passed:
                pc.status = "on_hold"
                pc.hold_reason = note
                if faulty:
                    reservations = Reservation.objects.select_for_update().filter(
                        purchased_pc=pc,
                        component=faulty,
                        status=Reservation.Status.RESERVED,
                    ).order_by("pk")
                    reservation = reservations.first()
                    if reservation:
                        available = available_component_quantity(faulty)
                        if available < 1:
                            if reservation.quantity == 1:
                                reservation.status = Reservation.Status.RELEASED
                                reservation.save(update_fields=("status",))
                            else:
                                reservation.quantity -= 1
                                reservation.save(update_fields=("quantity",))
                        StockAdjustment.objects.create(
                            item_type="component",
                            component=faulty,
                            delta=-1,
                            reason=f"Faulty part removed from PC #{pc.pk}: {note}",
                            created_by=request.user.employee_profile,
                        )
                        if available > 0:
                            pc.status = "corrective_work"
                            pc.hold_reason = "A working replacement is reserved; retest this check."
                        else:
                            pc.hold_reason = f"{note} No working unit is available; use a US-A11 substitution."
                pc.save(update_fields=("status", "hold_reason"))
                StaffAction.objects.create(
                    employee=request.user.employee_profile,
                    action_type="pc_quality_check_failed",
                    object_ref=str(pc.pk),
                    detail=f"{dict(QualityCheckForm.CHECKS)[check.checklist_item]}: {pc.hold_reason}",
                )
                messages.error(request, f"PC #{pc.pk} needs corrective work: {pc.hold_reason}")
            else:
                latest = {}
                checks = pc.quality_checks.all()
                last_approval = pc.substitutions.filter(status="accepted").order_by("-decided_at", "-pk").first()
                if last_approval:
                    checks = checks.filter(created_at__gt=last_approval.decided_at)
                for row in checks.order_by("created_at", "pk"):
                    latest[row.checklist_item] = row.passed
                required = {key for key, _ in QualityCheckForm.CHECKS}
                if required <= latest.keys() and all(latest[key] for key in required):
                    for reservation in Reservation.objects.select_for_update().filter(
                        purchased_pc=pc, status=Reservation.Status.RESERVED, component__isnull=False
                    ).select_related("component"):
                        StockAdjustment.objects.create(
                            item_type="component",
                            component=reservation.component,
                            delta=-reservation.quantity,
                            reason=f"PC #{pc.pk} passed quality checks; components installed.",
                            created_by=request.user.employee_profile,
                        )
                        reservation.status = Reservation.Status.CONSUMED
                        reservation.save(update_fields=("status",))
                    pc.status = "quality_passed"
                    pc.hold_reason = ""
                    pc.save(update_fields=("status", "hold_reason"))
                    StaffAction.objects.create(
                        employee=request.user.employee_profile,
                        action_type="pc_quality_passed",
                        object_ref=str(pc.pk),
                        detail=f"PC #{pc.pk} passed all quality checks; reserved components consumed.",
                    )
                    messages.success(request, f"PC #{pc.pk} passed quality checks.")
                elif pc.status == "corrective_work":
                    pc.status = "quality_checking"
                    pc.save(update_fields=("status",))
                    messages.success(request, "Check recorded; continue the remaining tests.")
                else:
                    messages.success(request, "Check recorded.")
    return redirect("orders:order_queue")


@require_POST
def hold_pc(request, pk):
    form = HoldPCForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a reason before placing the PC on hold.")
        return redirect("orders:order_queue")
    order_id = _order_id_for_pc(pk)
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=order_id)
        pc = get_object_or_404(PurchasedPC.objects.select_for_update(), pk=pk)
        if (
            order.payment_status != "paid"
            or order.status in ("cancelled", "delivered")
            or pc.status not in ("queued", "assembling")
        ):
            messages.error(request, "This PC cannot be put on hold in its current state.")
        else:
            pc.status = "on_hold"
            pc.hold_reason = form.cleaned_data["reason"]
            pc.save(update_fields=("status", "hold_reason"))
            StaffAction.objects.create(
                employee=request.user.employee_profile,
                action_type="pc_manual_hold",
                object_ref=str(pc.pk),
                detail=pc.hold_reason,
            )
            messages.success(request, f"PC #{pc.pk} is on hold.")
    return redirect("orders:order_queue")


@require_POST
def start_packing(request, pk):
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=pk)
        lines = list(order.lines.all())
        pcs = list(PurchasedPC.objects.filter(order_line__order=order))
        eligible_status = order.status in ("placed", "in_progress")
        if order.payment_status != "paid" or not lines:
            messages.error(request, "Only paid orders can enter the packing queue.")
        elif eligible_status and all(line.kind != "build" or line.pcs.count() == line.quantity for line in lines) and all(pc.status in ("quality_passed", "cancelled") for pc in pcs):
            issues = accessory_allocation_issues(order)
            if issues:
                messages.error(request, "Cannot start packing: " + " ".join(issues))
            else:
                previous_status = order.status
                order.status = "packing"
                order.save(update_fields=("status",))
                OrderStatusHistory.objects.create(
                    order=order,
                    from_status=previous_status,
                    to_status="packing",
                    note="Accessory-only order packing started.",
                    employee=request.user.employee_profile,
                )
                StaffAction.objects.create(
                    employee=request.user.employee_profile,
                    action_type="order_packing_started",
                    object_ref=str(order.pk),
                    detail=f"Started packing order #{order.pk}.",
                )
                messages.success(request, f"Packing started for order #{order.pk}.")
        else:
            messages.error(request, "This order is no longer waiting to be packed.")
    return redirect("orders:order_queue")


@require_POST
def dispatch_order(request, pk):
    form = DispatchForm(request.POST)
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=pk)
        lines = list(order.lines.all())
        pcs = list(PurchasedPC.objects.select_for_update().filter(order_line__order=order))
        if not form.is_valid():
            messages.error(request, "Enter valid courier and tracking details.")
        elif order.payment_status != "paid" or order.status != "packing" or not lines:
            messages.error(request, "This order is not ready for dispatch.")
        elif any(line.kind == "build" and line.pcs.count() != line.quantity for line in lines) or any(pc.status not in ("quality_passed", "cancelled") for pc in pcs):
            messages.error(request, "Every remaining PC must pass quality checks before dispatch.")
        else:
            issues = accessory_allocation_issues(order)
            if issues:
                messages.error(request, "Cannot dispatch: " + " ".join(issues))
            else:
                reservations = Reservation.objects.select_for_update().filter(
                    order_line__order=order,
                    status=Reservation.Status.RESERVED,
                    accessory__isnull=False,
                ).select_related("accessory")
                for reservation in reservations:
                    StockAdjustment.objects.create(
                        item_type="accessory",
                        accessory=reservation.accessory,
                        delta=-reservation.quantity,
                        reason=f"Accessory order #{order.pk} dispatched.",
                        created_by=request.user.employee_profile,
                    )
                    reservation.status = Reservation.Status.CONSUMED
                    reservation.save(update_fields=("status",))
                now = timezone.now()
                Shipment.objects.create(
                    order=order,
                    status="dispatched",
                    courier=form.cleaned_data["courier"],
                    tracking_ref=form.cleaned_data["tracking_ref"],
                    dispatched_at=now,
                )
                order.status = "dispatched"
                order.save(update_fields=("status",))
                OrderStatusHistory.objects.create(
                    order=order,
                    from_status="packing",
                    to_status="dispatched",
                    note="Order dispatched.",
                    employee=request.user.employee_profile,
                )
                StaffAction.objects.create(
                    employee=request.user.employee_profile,
                    action_type="order_dispatched",
                    object_ref=str(order.pk),
                    detail=f"Dispatched order #{order.pk}; tracking {form.cleaned_data['tracking_ref'] or 'not provided'}.",
                )
                messages.success(request, f"Order #{order.pk} dispatched.")
    return redirect("orders:order_queue")


def _confirm_delivery(request, order, confirmed_by, employee=None):
    shipment = get_object_or_404(Shipment.objects.select_for_update(), order=order)
    if shipment.status == "dispatched":
        shipment.status = "delivered"
        shipment.delivered_at = timezone.now()
        shipment.confirmed_by = confirmed_by
        shipment.save(update_fields=("status", "delivered_at", "confirmed_by"))
        order.status = "delivered"
        order.save(update_fields=("status",))
        OrderStatusHistory.objects.create(
            order=order,
            from_status="dispatched",
            to_status="delivered",
            note="Delivery confirmed.",
            employee=employee,
        )
        if employee:
            StaffAction.objects.create(
                employee=employee,
                action_type="order_delivery_confirmed",
                object_ref=str(order.pk),
                detail=f"Confirmed delivery for order #{order.pk}.",
            )
        messages.success(request, "Delivery confirmed.")
    else:
        messages.info(request, "This delivery has already been confirmed or is not dispatched.")


@require_POST
def staff_confirm_delivery(request, pk):
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=pk)
        employee = request.user.employee_profile
        _confirm_delivery(request, order, f"staff:{employee.pk}", employee)
    return redirect("orders:order_queue")


@login_required
@require_POST
def confirm_receipt(request, pk):
    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update(), pk=pk, customer__user=request.user
        )
        _confirm_delivery(request, order, f"customer:{request.user.pk}")
    return redirect("orders:order_detail", pk=pk)
