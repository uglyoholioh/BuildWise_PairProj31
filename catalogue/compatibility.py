from .models import Component, ComponentConflict, Configuration


REQUIRED_SPECS = {
    Component.Category.CPU: ("socket",),
    Component.Category.MOTHERBOARD: ("socket", "memory_type", "form_factor"),
    Component.Category.MEMORY: ("memory_type",),
    Component.Category.CASE: ("form_factor",),
}


def component_name(component):
    return f"{component.get_category_display()} {component.brand} {component.model}"


def check_compatibility(components):
    components = list(components)
    problems = []

    for component in components:
        if not component.active:
            problems.append(f"{component_name(component)} is inactive and cannot be sold.")
        for field in REQUIRED_SPECS.get(component.category, ()):
            if not getattr(component, field).strip():
                problems.append(f"{component_name(component)} is missing {field.replace('_', ' ')}.")

    cpus = [c for c in components if c.category == Component.Category.CPU]
    boards = [c for c in components if c.category == Component.Category.MOTHERBOARD]
    memory = [c for c in components if c.category == Component.Category.MEMORY]
    cases = [c for c in components if c.category == Component.Category.CASE]

    for cpu in cpus:
        for board in boards:
            if cpu.socket.strip() and board.socket.strip() and cpu.socket.strip().casefold() != board.socket.strip().casefold():
                problems.append(
                    f"{component_name(cpu)} socket {cpu.socket} does not match "
                    f"{component_name(board)} socket {board.socket}."
                )
    for board in boards:
        for ram in memory:
            if board.memory_type.strip() and ram.memory_type.strip() and board.memory_type.strip().casefold() != ram.memory_type.strip().casefold():
                problems.append(
                    f"{component_name(ram)} memory type {ram.memory_type} does not match "
                    f"{component_name(board)} memory type {board.memory_type}."
                )
        for case in cases:
            supported_form_factors = {value.strip().casefold() for value in case.form_factor.split(",")}
            if board.form_factor.strip() and board.form_factor.strip().casefold() not in supported_form_factors:
                problems.append(
                    f"{component_name(board)} form factor {board.form_factor} is not supported by "
                    f"{component_name(case)} ({case.form_factor})."
                )

    component_ids = {component.pk for component in components if component.pk}
    if component_ids:
        conflicts = ComponentConflict.objects.filter(
            component_a_id__in=component_ids, component_b_id__in=component_ids
        ).select_related("component_a", "component_b")
        problems.extend(
            f"{component_name(conflict.component_a)} and {component_name(conflict.component_b)} "
            f"are marked incompatible: {conflict.reason}"
            for conflict in conflicts
        )
    return problems


def check_configuration(configuration):
    return check_compatibility(configuration.components.all())


def revalidate_configurations(components):
    component_ids = [component.pk for component in components if component.pk]
    if not component_ids:
        return
    affected = Configuration.objects.filter(
        status=Configuration.Status.PUBLISHED, components__pk__in=component_ids
    ).distinct()
    for configuration in affected:
        if check_configuration(configuration):
            configuration.status = Configuration.Status.DRAFT
            configuration.save(update_fields=("status",))
