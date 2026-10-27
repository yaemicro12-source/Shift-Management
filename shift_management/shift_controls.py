import calendar
from datetime import date, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, F
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from jpholiday import JPHoliday

from .decorators import manager_required
from .models import (
    Area,
    Store,
    ManagementScope,
    ShiftSchedule,
    ShiftAssignment,
    ShiftShortage,
    ShiftTimeSlot,
    RequiredStaffingRule,
)


def allowed_stores(manager):
    scopes = ManagementScope.objects.filter(manager=manager)

    return Store.objects.filter(
        Q(area_id__in=scopes.values("area_id"))
        | Q(id__in=scopes.values("store_id"))
    ).distinct()


def controlled_stores(manager, area):
    stores = Store.objects.filter(area=area).order_by("id")
    allowed_ids = set(
        allowed_stores(manager).values_list("id", flat=True)
    )

    if not stores.exists():
        raise ValidationError("対象エリアに店舗がありません。")

    if not set(
        stores.values_list("id", flat=True)
    ).issubset(allowed_ids):
        raise PermissionDenied(
            "この操作にはエリア全店舗の管理権限が必要です。"
        )

    return stores


def get_month_schedule(area, year, month):
    rows = list(
        ShiftSchedule.objects.filter(
            area=area,
            year=year,
            month=month,
        )
    )

    if len(rows) > 1:
        raise ValidationError(
            "このエリア・月の作成情報が重複しています。"
        )

    return rows[0] if rows else None


def require_draft_month(store, work_date):
    schedule = get_month_schedule(
        store.area,
        work_date.year,
        work_date.month,
    )

    if schedule is None or schedule.status != 2:
        raise ValidationError(
            "手動登録・変更は、自動生成済みの下書きの月だけ利用できます。"
        )

    return schedule


def minutes(value, end=False):
    result = value.hour * 60 + value.minute
    return 1440 if end and result == 0 else result


def required_count(day, slot):
    rules = list(
        RequiredStaffingRule.objects.filter(
            store=slot.store,
            shift_time_slot=slot,
            target_date=day,
        )
    )

    if len(rules) > 1:
        raise ValidationError(
            f"{day} {slot.store} {slot.name}："
            "特定日の必要人数が重複しています。"
        )

    if rules:
        return rules[0].required_count

    defaults = {"朝番": 1, "昼番": 2, "夜番": 2}

    if slot.name not in defaults:
        raise ValidationError(
            f"{slot.store} {slot.name}の必要人数を設定してください。"
        )

    holiday = day.weekday() >= 5 or JPHoliday().is_holiday(day)
    return defaults[slot.name] + int(holiday)


def refresh_day_shortages(stores, day):
    from .staff_requests import recalculate_shortages
    recalculate_shortages(stores, day)

@manager_required
def shift_management_page(request):
    manager = request.user.staffprofile
    areas = Area.objects.filter(
        store__in=allowed_stores(manager)
    ).distinct().order_by("id")

    default = (
        timezone.localdate().replace(day=1)
        + timedelta(days=32)
    ).replace(day=1)

    params = request.POST if request.method == "POST" else request.GET

    try:
        year = int(params.get("year", default.year))
        month = int(params.get("month", default.month))
        if not 2000 <= year <= 2100:
            raise ValueError

        first = date(year, month, 1)
        last = date(
            year, month, calendar.monthrange(year, month)[1]
        )
        first_area = areas.first()
        area_id = int(
            params.get("area", "")
            or (first_area.id if first_area else 0)
        )
    except (ValueError, TypeError):
        messages.error(request, "対象年月を正しく選択してください。")
        return redirect("shift_management:shift_management")

    area = (
        get_object_or_404(areas, id=area_id)
        if area_id else None
    )

    if area is None:
        return render(
            request,
            "shift_management/shift_management.html",
            {"areas": areas, "year": year, "month": month},
        )

    stores = controlled_stores(manager, area)
    destination = (
        reverse("shift_management:shift_management")
        + "?"
        + urlencode({
            "year": year,
            "month": month,
            "area": area.id,
        })
    )

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "generate":
            # 循環importを避けるため、ここで読み込む
            from .views import generate_shift
            return generate_shift(request)

        try:
            with transaction.atomic():
                Area.objects.select_for_update().get(pk=area.pk)
                schedule = get_month_schedule(area, year, month)

                shifts = ShiftAssignment.objects.filter(
                    store__in=stores,
                    work_date__range=(first, last),
                )

                if action == "delete":
                    if (
                        shifts.filter(status=2).exists()
                        or (schedule and schedule.status >= 3)
                    ):
                        raise ValidationError(
                            "確定・公開済みのシフトは削除できません。"
                        )

                    count = shifts.filter(status=1).count()
                    shifts.filter(status=1).delete()

                    ShiftShortage.objects.filter(
                        store__in=stores,
                        target_date__range=(first, last),
                    ).delete()

                    if schedule:
                        schedule.status = 1
                        schedule.generated_at = None
                        schedule.published_at = None
                        schedule.save()

                    messages.success(
                        request,
                        f"下書き{count}件を削除し、未作成に戻しました。"
                        "勤務希望は残しています。",
                    )

                elif action == "confirm":
                    if schedule is None or schedule.status != 2:
                        raise ValidationError(
                            "自動生成済みの下書きだけ確定できます。"
                        )

                    shortages = ShiftShortage.objects.filter(
                        store__in=stores,
                        target_date__range=(first, last),
                        assigned_count__lt=F("required_count"),
                    )

                    if (
                        shortages.exists()
                        and request.POST.get("accept_shortage") != "yes"
                    ):
                        raise ValidationError(
                            "不足が残っています。確認チェックを入れてください。"
                        )

                    count = shifts.filter(status=1).update(status=2)
                    schedule.status = 3
                    schedule.save(update_fields=["status"])

                    messages.success(
                        request,
                        f"{count}件を確定しました。公開は別の操作です。",
                    )

                elif action == "publish":
                    if schedule is None or schedule.status != 3:
                        raise ValidationError(
                            "確定済みの月だけ公開できます。"
                        )

                    if shifts.filter(status=1).exists():
                        raise ValidationError(
                            "未確定の下書きが残っています。"
                        )

                    schedule.status = 4
                    schedule.published_at = timezone.now()
                    schedule.save(
                        update_fields=["status", "published_at"]
                    )
                    messages.success(request, "シフトを公開しました。")

                else:
                    raise ValidationError("操作が不正です。")

        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))

        return redirect(destination)

    schedule = get_month_schedule(area, year, month)
    shifts = ShiftAssignment.objects.filter(
        store__in=stores,
        work_date__range=(first, last),
    )

    draft_count = shifts.filter(status=1).count()
    confirmed_count = shifts.filter(status=2).count()
    state = schedule.status if schedule else 1

    shortages = list(
        ShiftShortage.objects.filter(
            store__in=stores,
            target_date__range=(first, last),
            assigned_count__lt=F("required_count"),
        ).select_related(
            "store", "shift_time_slot"
        ).order_by("target_date", "store_id", "id")
    )

    for shortage in shortages:
        shortage.missing_count = (
            shortage.required_count - shortage.assigned_count
        )

    return render(
        request,
        "shift_management/shift_management.html",
        {
            "areas": areas,
            "selected_area": area,
            "year": year,
            "month": month,
            "months": range(1, 13),
            "first": first,
            "state": state,
            "state_label": {
                1: "未作成",
                2: "下書き",
                3: "確定済み",
                4: "公開済み",
            }[state],
            "draft_count": draft_count,
            "confirmed_count": confirmed_count,
            "shortages": shortages,
            "can_generate": (
                state == 1
                and draft_count == 0
                and confirmed_count == 0
                and first >= timezone.localdate().replace(day=1)
            ),
            "can_delete": (
                state <= 2
                and confirmed_count == 0
                and bool(draft_count or schedule)
            ),
        },
    )