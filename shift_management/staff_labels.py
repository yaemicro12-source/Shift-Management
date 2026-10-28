from collections import defaultdict

from django.db.models import Q

from .models import StaffStoreAssignment


def name_parts(staff):
    raw = " ".join(staff.name.split())
    given = "".join((staff.firstname or "").split())
    compact = "".join(raw.split())

    if given and compact.endswith(given) and compact != given:
        surname = compact[:-len(given)]
    elif not given and " " in raw:
        surname, given = raw.split(" ", 1)
        given = "".join(given.split())
    else:
        surname = compact

    return surname, given


def chart_names(staffs, day, stores):
    areas = {store.area_id for store in stores}

    memberships = (
        StaffStoreAssignment.objects.filter(
            store__area_id__in=areas,
            staff__is_active=True,
            starts_on__lte=day,
        )
        .filter(
            Q(ends_on__isnull=True)
            | Q(ends_on__gte=day)
        )
        .select_related("staff", "store")
    )

    staff_areas = defaultdict(set)
    groups = defaultdict(set)
    initials = defaultdict(set)
    fullnames = defaultdict(set)

    for row in memberships:
        surname, given = name_parts(row.staff)
        area = row.store.area_id

        staff_areas[row.staff_id].add(area)
        groups[area, surname].add(row.staff_id)
        initials[area, surname, given[:1]].add(row.staff_id)
        fullnames[area, surname, given].add(row.staff_id)

    result = {}

    for staff in staffs:
        surname, given = name_parts(staff)
        full = f"{surname} {given}".strip()
        own_areas = staff_areas[staff.pk]

        duplicated = any(
            len(groups[area, surname]) > 1
            for area in own_areas
        )

        label = surname

        if duplicated:
            label = (
                f"{surname} {given[:1]}"
                if given else f"{surname}（{staff.pk}）"
            )

            if given and any(
                len(initials[area, surname, given[:1]]) > 1
                for area in own_areas
            ):
                label = full

            if given and any(
                len(fullnames[area, surname, given]) > 1
                for area in own_areas
            ):
                label = f"{full}（{staff.pk}）"

        result[staff.pk] = {
            "short": label,
            "full": full,
        }

    return result


def prepare_chart_staffs(
    items, shifts, day, stores, selected_store=None
):
    stores = list(stores)
    store_map = {store.pk: store for store in stores}
    store_order = {
        store.pk: index
        for index, store in enumerate(stores)
    }

    labels = chart_names(
        [item["staff"] for item in items],
        day,
        [selected_store] if selected_store else stores,
    )

    memberships = (
        StaffStoreAssignment.objects.filter(
            store_id__in=store_map,
            starts_on__lte=day,
        )
        .filter(
            Q(ends_on__isnull=True)
            | Q(ends_on__gte=day)
        )
        .order_by("-is_primary", "store_id", "pk")
    )

    groups = {}

    for row in memberships:
        groups.setdefault(row.staff_id, row.store_id)

    # 実際にその日配置されている店舗を優先する
    assigned = {}

    for shift in sorted(
        shifts,
        key=lambda row: (
            store_order.get(row.store_id, len(stores)),
            row.pk,
        ),
    ):
        if shift.staff_id and shift.store_id in store_map:
            assigned.setdefault(shift.staff_id, shift.store_id)

    for item in items:
        staff_id = item["staff"].pk
        store_id = assigned.get(
            staff_id,
            groups.get(staff_id),
        )

        item.update(
            display_name=labels[staff_id]["short"],
            full_name=labels[staff_id]["full"],
            store_order=(
                store_order.get(store_id, len(stores))
                if not selected_store else 0
            ),
            store_name=(
                store_map[store_id].name
                if store_id in store_map else "所属未設定"
            ),
            show_store=selected_store is None,
        )

    items.sort(
        key=lambda item: (
            item["store_order"],
            item["sort_start"],
            item["sort_order"],
            item["staff"].pk,
        )
    )

    return items