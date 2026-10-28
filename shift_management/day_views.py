from datetime import date

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone

from .models import (
    StaffProfile,
    Store,
    StaffStoreAssignment,
    ShiftAssignment,
    ShiftSchedule,
)
from .shift_controls import allowed_stores


@login_required(login_url="shift_management:staff_login")
def day_shift_detail(request, year, month, day):
    try:
        target = date(year, month, day)
    except ValueError:
        return HttpResponseBadRequest("日付が不正です。")

    staff = get_object_or_404(
        StaffProfile,
        user=request.user,
        is_active=True,
    )

    if staff.role == 2:
        shifts = ShiftAssignment.objects.filter(
            store__in=allowed_stores(staff),
            status__in=[1, 2],
        )
        home = "shift_management:manager_main"

    else:
        home = "shift_management:staff_main"

        memberships = StaffStoreAssignment.objects.filter(
            staff=staff,
            starts_on__lte=target,
        ).filter(
            Q(ends_on__isnull=True)
            | Q(ends_on__gte=target)
        )

        own_stores = ShiftAssignment.objects.filter(
            staff=staff,
            work_date=target,
            status=2,
        ).values("store_id")

        stores = Store.objects.filter(
            Q(pk__in=memberships.values("store_id"))
            | Q(pk__in=own_stores)
        )

        shifts = ShiftAssignment.objects.filter(
            store__in=stores,
            status=2,
        )

        if not staff.can_view_all_shifts:
            shifts = shifts.filter(staff=staff)

        # メイン画面と同様、未来の月は公開済みだけ
        today = timezone.localdate()

        if (year, month) > (today.year, today.month):
            published = ShiftSchedule.objects.filter(
                year=year,
                month=month,
                status=4,
            ).values("area_id")

            shifts = shifts.filter(
                store__area_id__in=published
            )

    shifts = shifts.filter(
        work_date=target,
        staff__isnull=False,
    ).select_related(
        "store",
        "staff",
        "shift_time_slot",
    )

    rows = []

    for shift in shifts:
        slot = shift.shift_time_slot

        start = shift.start_time
        end = shift.end_time

        if slot:
            if start is None:
                start = slot.start_time
            if end is None:
                end = slot.end_time

        slot_start = slot.start_time if slot else start

        rows.append({
            "store": shift.store,
            "staff": shift.staff,
            "slot": slot.name if slot else "手動枠",
            "start": (
                start.strftime("%H:%M")
                if start is not None else "未設定"
            ),
            "end": (
                "24:00"
                if end is not None
                and end.hour == 0
                and end.minute == 0
                else end.strftime("%H:%M")
                if end is not None else "未設定"
            ),
            "training": (
                shift.is_training or shift.staff.is_trainee
            ),
            "draft": shift.status == 1,
            "sort": (
                shift.store.name,
                shift.store_id,
                slot_start.hour * 60 + slot_start.minute
                if slot_start is not None else 9999,
                start.hour * 60 + start.minute
                if start is not None else 9999,
                shift.staff.name,
                shift.pk,
            ),
        })

    rows.sort(key=lambda row: row["sort"])

    today = timezone.localdate()
    offset = (
        (year - today.year) * 12
        + month - today.month
    )

    return render(
        request,
        "shift_management/day_shift_detail.html",
        {
            "target_date": target,
            "rows": rows,
            "home_url": (
                reverse(home)
                + f"?month={max(-1, min(1, offset))}"
            ),
        },
    )