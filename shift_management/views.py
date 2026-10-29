from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from datetime import datetime, date, timedelta
import calendar
from .models import ShiftAssignment,Area,EvaluationSetting, StaffEvaluation
from .models import StaffProfile ,Store,ShiftTimeSlot,ManagementScope,ShiftSubmission,ShiftPreference,RequiredStaffingRule,StaffStoreAssignment,ShiftShortage
from django.http import HttpResponseBadRequest
from .forms import ShiftAssignmentForm,StaffProfileForm,StaffEditForm,StaffLoginForm,ManagerLoginForm
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from django.contrib.auth import login,logout
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from .decorators import manager_required
from .decorators import manager_required, staff_required
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from .models import EvaluationItem
from .models import ShiftSchedule
from datetime import time
from django.core.exceptions import ValidationError
from django.db.models import F
from django.urls import reverse
from collections import defaultdict
from .staff_requests import reject_past_generation

from .shift_controls import (
    allowed_stores,
    controlled_stores,
    get_month_schedule,
    require_draft_month,
    refresh_day_shortages,
    required_count as staffing_count,
)

def get_evaluation_areas(evaluator, year, month):
    """
    評価者が評価できるエリアを取得する。

    管理者：ManagementScopeで管理しているエリア
    役職者：対象月に確定シフトで勤務したエリア
    """

    if evaluator.role == 2:
        scopes = ManagementScope.objects.filter(
            manager=evaluator
        )

        return Area.objects.filter(
            Q(managementscope__in=scopes)
            | Q(store__managementscope__in=scopes)
        ).distinct().order_by("name")

    if evaluator.is_position_holder:
        area_ids = ShiftAssignment.objects.filter(
            staff=evaluator,
            work_date__year=year,
            work_date__month=month,
            status=2,
            store__isnull=False
        ).values_list(
            "store__area_id",
            flat=True
        ).distinct()

        return Area.objects.filter(
            id__in=area_ids
        ).order_by("name")

    return Area.objects.none()


def get_evaluation_targets(evaluator, year, month, area):
    """
    指定月に評価者と勤務時間が重なった
    一般スタッフを抽出する。
    """

    # 管理者または役職者だけが評価可能
    if evaluator.role != 2 and not evaluator.is_position_holder:
        return []

    # 評価者が実際に勤務した確定シフト
    evaluator_shifts = ShiftAssignment.objects.filter(
        staff=evaluator,
        store__area=area,
        work_date__year=year,
        work_date__month=month,
        status=2
    ).select_related("store", "shift_time_slot")

    target_ids = set()

    for evaluator_shift in evaluator_shifts:
        # 同じ店舗・同じ日の一般スタッフ
        staff_shifts = ShiftAssignment.objects.filter(
            store=evaluator_shift.store,
            work_date=evaluator_shift.work_date,
            work_date__year=year,
            work_date__month=month,
            status=2,
            staff__role=1,
            staff__is_position_holder=False,
            staff__is_active=True
        ).exclude(
            staff=evaluator
        ).select_related("staff", "shift_time_slot")

        for staff_shift in staff_shifts:
            # 個別時刻がなければシフト枠の時刻を使用
            evaluator_start = (
                evaluator_shift.start_time
                or (
                    evaluator_shift.shift_time_slot.start_time
                    if evaluator_shift.shift_time_slot else None
                )
            )
            evaluator_end = (
                evaluator_shift.end_time
                or (
                    evaluator_shift.shift_time_slot.end_time
                    if evaluator_shift.shift_time_slot else None
                )
            )
            staff_start = (
                staff_shift.start_time
                or (
                    staff_shift.shift_time_slot.start_time
                    if staff_shift.shift_time_slot else None
                )
            )
            staff_end = (
                staff_shift.end_time
                or (
                    staff_shift.shift_time_slot.end_time
                    if staff_shift.shift_time_slot else None
                )
            )

            if None in (
                evaluator_start,
                evaluator_end,
                staff_start,
                staff_end
            ):
                continue

            # 深夜をまたぐ勤務にも対応
            evaluator_start_dt = datetime.combine(
                evaluator_shift.work_date, evaluator_start
            )
            evaluator_end_dt = datetime.combine(
                evaluator_shift.work_date, evaluator_end
            )
            staff_start_dt = datetime.combine(
                staff_shift.work_date, staff_start
            )
            staff_end_dt = datetime.combine(
                staff_shift.work_date, staff_end
            )

            if evaluator_end_dt <= evaluator_start_dt:
                evaluator_end_dt += timedelta(days=1)

            if staff_end_dt <= staff_start_dt:
                staff_end_dt += timedelta(days=1)

            # 1分以上重なるスタッフだけを追加
            overlap_start = max(
                evaluator_start_dt, staff_start_dt
            )
            overlap_end = min(
                evaluator_end_dt, staff_end_dt
            )

            if (overlap_end - overlap_start).total_seconds() >= 60:
                target_ids.add(staff_shift.staff_id)

    return StaffProfile.objects.filter(
        id__in=target_ids
    ).order_by("name")

def get_calendar_data(year = None,month = None,day = None): 
    if year == None:
        today = date.today()
        year = today.year
        month = today.month
        day = today.day

    if day is None:
        day = date.today().day

    cal = calendar.Calendar()


    weeks = cal.monthdayscalendar(
        year,
        month,
        )

    return{
        "day": day,
        "month":month, 
        "weeks":weeks,
        "year":year
    }

def get_main_shift_context(request):
    today = timezone.localdate()

    try:
        offset = int(request.GET.get("month", "0"))
    except (ValueError, TypeError):
        offset = 0

    offset = max(-1, min(1, offset))

    # 前月・当月・次月を計算
    total_months = today.year * 12 + today.month - 1 + offset
    year, month_index = divmod(total_months, 12)
    month = month_index + 1

    context = get_calendar_data(year, month)

    shifts = ShiftAssignment.objects.filter(
        staff__user=request.user,
        work_date__year=year,
        work_date__month=month,
        status=2,
    ).select_related(
        "store",
        "shift_time_slot",
    ).order_by("work_date", "start_time")

    # 次月は公開済みのエリアのシフトだけ表示
    if offset == 1:
        published_area_ids = ShiftSchedule.objects.filter(
            year=year,
            month=month,
            status=4,
        ).values_list("area_id", flat=True)

        shifts = shifts.filter(
            store__area_id__in=published_area_ids
        )

        is_published = ShiftSchedule.objects.filter(
            year=year,
            month=month,
            status=4,
            area_id__in=StaffStoreAssignment.objects.filter(
                staff__user=request.user,
                starts_on__lte=date(year, month, calendar.monthrange(year, month)[1]),
            ).filter(
                Q(ends_on__isnull=True) |
                Q(ends_on__gte=date(year, month, 1))
            ).values_list("store__area_id", flat=True)
        ).exists()
    else:
        is_published = True

    context.update({
        "staff_name": request.user.staffprofile.name,
        "shifts": shifts,
        "month_offset": offset,
        "can_go_previous": offset > -1,
        "can_go_next": offset < 1,
        "is_published": is_published,
        "is_current_month": offset == 0,
        "today": today,
        "today_date": today,
        "today_shifts": ShiftAssignment.objects.filter(
            staff__user=request.user,
            work_date=today,
            status=2,
        ).select_related("store", "shift_time_slot"),
    })

    return context

@staff_required
def staff_main(request):
    context = get_main_shift_context(request)

    context["can_evaluate"] = (
        request.user.staffprofile.role == 2
        or request.user.staffprofile.is_position_holder
    )

    return render(
        request,
        "shift_management/staff_main.html",
        context,
    )

#シフト提出
@login_required(login_url="shift_management:staff_login")
def shift_submission(request):
    import calendar
    from datetime import date, time, timedelta

    from django.db import transaction
    from django.utils import timezone
    from jpholiday import JPHoliday

    today = timezone.localdate()
    first = (
        today.replace(day=1) + timedelta(days=32)
    ).replace(day=1)
    year, month = first.year, first.month

    staff = get_object_or_404(
        StaffProfile,
        user=request.user,
        is_active=True,
    )
    holidays = JPHoliday()

    latest = ShiftSubmission.objects.filter(
        staff=staff,
        year=year,
        month=month,
        is_latest=True,
        status__in=[2, 3],
    ).order_by("-pk").first()

    saved = {
        row.target_date.day: row
        for row in ShiftPreference.objects.filter(
            submission=latest
        )
    } if latest else {}

    def clock(value):
        return value.strftime("%H:%M") if value is not None else ""

    days = []
    errors = []

    for number in range(
        1, calendar.monthrange(year, month)[1] + 1
    ):
        day = date(year, month, number)
        old = saved.get(number)

        checked = (
            old.is_available
            if staff.submission_type == 1
            else not old.is_available
        ) if old else False

        item = {
            "day": number,
            "weekday": "月火水木金土日"[day.weekday()],
            "weekday_code": day.weekday(),
            "is_holiday": holidays.is_holiday(day),
            "checked": checked,
            "start": clock(old.available_start_time) if old else "",
            "end": clock(old.available_end_time) if old else "",
            "note": old.note if old else "",
        }

        if request.method == "POST":
            item.update(
                checked=f"day_{number}" in request.POST,
                start=request.POST.get(
                    f"start_{number}", ""
                ).strip(),
                end=request.POST.get(
                    f"end_{number}", ""
                ).strip(),
                note=request.POST.get(
                    f"note_{number}", ""
                ).strip(),
            )

            available = (
                item["checked"]
                if staff.submission_type == 1
                else not item["checked"]
            )
            item["available"] = available
            item["start_value"] = None
            item["end_value"] = None

            if available and (item["start"] or item["end"]):
                try:
                    start = time.fromisoformat(item["start"])
                    end = time.fromisoformat(item["end"])

                    start_minutes = start.hour * 60 + start.minute
                    end_minutes = (
                        end.hour * 60 + end.minute
                    ) or 1440

                    if (
                        start.tzinfo
                        or end.tzinfo
                        or start.second
                        or start.microsecond
                        or end.second
                        or end.microsecond
                        or end_minutes <= start_minutes
                    ):
                        raise ValueError

                    item["start_value"] = start
                    item["end_value"] = end

                except ValueError:
                    errors.append(
                        f"{number}日：出勤可能時間を正しく"
                        "入力してください。終了の00:00は"
                        "24:00として扱います。"
                    )

        days.append(item)

    if request.method == "POST" and not errors:
        with transaction.atomic():
            # 同じスタッフによる同時提出を順番に処理
            StaffProfile.objects.select_for_update().get(
                pk=staff.pk
            )

            latest = ShiftSubmission.objects.filter(
                staff=staff,
                year=year,
                month=month,
                is_latest=True,
                status__in=[2, 3],
            ).order_by("-pk").first()

            values = [
                (
                    date(year, month, row["day"]),
                    row["available"],
                    row["start_value"],
                    row["end_value"],
                    row["note"],
                )
                for row in days
            ]

            same = (
                latest is not None
                and latest.submission_type == staff.submission_type
                and list(
                    ShiftPreference.objects.filter(
                        submission=latest
                    ).order_by(
                        "target_date", "pk"
                    ).values_list(
                        "target_date",
                        "is_available",
                        "available_start_time",
                        "available_end_time",
                        "note",
                    )
                ) == values
            )

            if not same:
                # 過去の提出は削除せず履歴として残す
                ShiftSubmission.objects.filter(
                    staff=staff,
                    year=year,
                    month=month,
                ).update(is_latest=False)

                submission = ShiftSubmission.objects.create(
                    staff=staff,
                    year=year,
                    month=month,
                    submission_type=staff.submission_type,
                    status=2,
                    is_latest=True,
                    submitted_at=timezone.now(),
                )

                ShiftPreference.objects.bulk_create([
                    ShiftPreference(
                        submission=submission,
                        target_date=value[0],
                        is_available=value[1],
                        available_start_time=value[2],
                        available_end_time=value[3],
                        note=value[4],
                    )
                    for value in values
                ])

        messages.success(
            request,
            "同じ内容がすでに提出されています。"
            if same else "希望シフトを提出しました。",
        )

        available_count = sum(
            row["available"] for row in days
        )

        if (
            staff.show_submission_warning
            and available_count < staff.minimum_available_days
        ):
            messages.warning(
                request,
                "提出は保存しましたが、出勤可能日数が"
                f"最低提出日数の{staff.minimum_available_days}日"
                "を下回っています。",
            )

        # 再読み込みによるPOST再送信を防ぐ
        return redirect("shift_management:sift_submission")

    previous_month = first - timedelta(days=1)

    previous = ShiftSubmission.objects.filter(
        staff=staff,
        year=previous_month.year,
        month=previous_month.month,
        is_latest=True,
        status__in=[2, 3],
    ).order_by("-pk").first()

    previous_settings = {}

    if previous:
        for row in ShiftPreference.objects.filter(
            submission=previous
        ).order_by("target_date", "pk"):
            # 祝日は通常の曜日に混ぜない
            key = (
                "holiday"
                if holidays.is_holiday(row.target_date)
                else str(row.target_date.weekday())
            )

            previous_settings[key] = {
                "available": row.is_available,
                "start": clock(row.available_start_time),
                "end": clock(row.available_end_time),
            }

    return render(
        request,
        "shift_management/shift_submission.html",
        {
            "year": year,
            "month": month,
            "days": days,
            "errors": errors,
            "submission_type": staff.get_submission_type_display(),
            "submission_type_code": staff.submission_type,
            "previous_settings": previous_settings,
            "has_submission": latest is not None,
        },
    )

@manager_required
def edit_shift(request):
    """下書きシフトの編集・希望一覧・不足日の表示。"""
    manager = request.user.staffprofile
    editable_stores = allowed_stores(manager)
    stores = editable_stores.order_by("id")

    def minute(value, end=False):
        result = value.hour * 60 + value.minute
        return 1440 if end and result == 0 else result

    def time_text(value, end=False):
        if end and value == time(0, 0):
            return "24:00"
        return value.strftime("%H:%M")

    # 表示する日付
    try:
        if request.GET.get("date"):
            selected_date = date.fromisoformat(
                request.GET["date"]
            )
        elif request.session.get("year") and request.session.get("month"):
            selected_date = date(
                int(request.session["year"]),
                int(request.session["month"]),
                1,
            )
        else:
            selected_date = (
                timezone.localdate().replace(day=1)
                + timedelta(days=32)
            ).replace(day=1)
    except (ValueError, TypeError):
        return HttpResponseBadRequest("日付が不正です。")

    year = selected_date.year
    month = selected_date.month
    day = selected_date.day

    request.session["year"] = year
    request.session["month"] = month

    # 店舗フィルター
    selected_store_id = request.GET.get("store", "")
    selected_store = None

    if selected_store_id:
        try:
            selected_store = get_object_or_404(
                editable_stores,
                pk=int(selected_store_id),
            )
        except (ValueError, TypeError):
            return HttpResponseBadRequest("店舗が不正です。")

    # 編集対象
    selected_shift = None
    shift_id = request.GET.get("shift_id")

    if shift_id:
        try:
            selected_shift = get_object_or_404(
                ShiftAssignment.objects.select_related(
                    "store__area", "shift_time_slot"
                ),
                pk=int(shift_id),
                store__in=editable_stores,
            )
        except (ValueError, TypeError):
            return HttpResponseBadRequest("シフトIDが不正です。")

    staff_queryset = StaffProfile.objects.filter(
        Q(staffstoreassignment__store__in=editable_stores)
        | Q(shift_assignments__store__in=editable_stores)
    ).distinct().order_by("id")

    # 個別削除
    if request.method == "POST" and request.POST.get("action") == "delete":
        if selected_shift is None:
            messages.error(request, "削除するシフトを選択してください。")
            return redirect(request.get_full_path())

        try:
            with transaction.atomic():
                Area.objects.select_for_update().get(
                    pk=selected_shift.store.area_id
                )

                state = get_month_schedule(
                    selected_shift.store.area,
                    selected_shift.work_date.year,
                    selected_shift.work_date.month,
                )

                if (
                    selected_shift.status != 1
                    or (state and state.status >= 3)
                ):
                    raise ValidationError(
                        "削除できるのは下書きだけです。"
                    )

                deleted_date = selected_shift.work_date
                deleted_store_id = selected_shift.store_id

                selected_shift.delete()

                refresh_day_shortages(
                    list(editable_stores),
                    deleted_date,
                )

        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect(request.get_full_path())

        messages.success(request, "下書きを削除しました。")

        return redirect(
            f"{reverse('shift_management:edit_shift')}"
            f"?date={deleted_date:%Y-%m-%d}"
            f"&store={deleted_store_id}"
        )

    # 登録・変更
    if request.method == "POST":
        form = ShiftAssignmentForm(
            request.POST,
            instance=selected_shift,
        )
    else:
        form = ShiftAssignmentForm(
            instance=selected_shift,
            initial={"work_date": selected_date.isoformat()},
        )

    form.fields["store"].queryset = editable_stores
    form.fields["staff"].queryset = staff_queryset
    form.fields["shift_time_slot"].queryset = (
        ShiftTimeSlot.objects.filter(store__in=editable_stores)
    )

    if (
        request.method == "POST"
        and request.POST.get("action") == "save"
        and form.is_valid()
    ):
        shift = form.save(commit=False)

        try:
            with transaction.atomic():
                targets = [(shift.store, shift.work_date)]

                if shift.pk:
                    original = ShiftAssignment.objects.select_related(
                        "store__area"
                    ).get(
                        pk=shift.pk,
                        store__in=editable_stores,
                    )

                    if original.status != 1:
                        raise ValidationError(
                            "確定済みのシフトは変更できません。"
                        )

                    targets.append(
                        (original.store, original.work_date)
                    )

                # 変更前・変更後の両方を確認
                for target_store, target_day in sorted(
                    targets,
                    key=lambda item: (
                        item[0].area_id,
                        item[1],
                    ),
                ):
                    Area.objects.select_for_update().get(
                        pk=target_store.area_id
                    )
                    require_draft_month(
                        target_store,
                        target_day,
                    )

                if (
                    shift.shift_time_slot
                    and shift.shift_time_slot.store_id
                    != shift.store_id
                ):
                    raise ValidationError(
                        "店舗とシフト枠が一致していません。"
                    )

                belongs = StaffStoreAssignment.objects.filter(
                    staff=shift.staff,
                    store=shift.store,
                    starts_on__lte=shift.work_date,
                ).filter(
                    Q(ends_on__isnull=True)
                    | Q(ends_on__gte=shift.work_date)
                ).exists()

                if not belongs:
                    raise ValidationError(
                        "このスタッフは選択日の店舗に所属していません。"
                    )

                shift.schedule = require_draft_month(
                    shift.store,
                    shift.work_date,
                )
                shift.status = 1
                shift.save()

                for target_day in {
                    value[1] for value in targets
                }:
                    refresh_day_shortages(
                        list(editable_stores),
                        target_day,
                    )

        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))

        else:
            messages.success(request, "下書きを保存しました。")

            return redirect(
                f"{reverse('shift_management:edit_shift')}"
                f"?date={shift.work_date:%Y-%m-%d}"
                f"&store={shift.store_id}"
            )

    # 選択日のシフト
    day_shifts_query = ShiftAssignment.objects.filter(
        store__in=editable_stores,
        work_date=selected_date,
        status__in=[1, 2],
    )

    if selected_store:
        day_shifts_query = day_shifts_query.filter(
            store=selected_store
        )

    day_shifts = list(
        day_shifts_query.select_related(
            "staff", "shift_time_slot"
        ).order_by("id")
    )

    staffs = list(staff_queryset)

    # グラフ範囲：既存の24時以降の表示方法を維持
    store_hours = []

    for store in stores:
        opening = minute(store.opening_time)
        closing = minute(store.closing_time)

        if closing <= opening:
            closing += 1440

        store_hours.append({
            "id": store.id,
            "opening": opening,
            "closing": closing,
        })

    hours_data = next(
        (
            item for item in store_hours
            if selected_store and item["id"] == selected_store.id
        ),
        None,
    )

    if hours_data:
        chart_start = hours_data["opening"]
        chart_end = hours_data["closing"]
    elif store_hours:
        chart_start = min(item["opening"] for item in store_hours)
        chart_end = max(item["closing"] for item in store_hours)
    else:
        chart_start = 6 * 60
        chart_end = 24 * 60

    pixels_per_hour = 24
    chart_start_hour = (chart_start - 60) // 60
    chart_end_hour = (chart_end + 60 + 59) // 60
    chart_height = (
        chart_end_hour - chart_start_hour
    ) * pixels_per_hour

    chart_hours = [
        {
            "label": f"{hour:02d}:00",
            "top": (
                hour - chart_start_hour
            ) * pixels_per_hour,
        }
        for hour in range(
            chart_start_hour,
            chart_end_hour + 1,
        )
    ]

    chart_staffs = []

    for staff in staffs:
        staff_shifts = [
            shift for shift in day_shifts
            if shift.staff_id == staff.id
        ]
        bars = []

        for shift in staff_shifts:
            if shift.start_time is None or shift.end_time is None:
                continue

            start = minute(shift.start_time)
            end = minute(shift.end_time)

            if end <= start:
                end += 1440

            if (
                chart_end_hour > 24
                and start < chart_start_hour * 60
            ):
                start += 1440
                end += 1440

            top = (
                start - chart_start_hour * 60
            ) * pixels_per_hour / 60
            height = (
                end - start
            ) * pixels_per_hour / 60

            visible_top = max(0, top)
            visible_bottom = min(
                chart_height, top + height
            )

            if visible_bottom <= visible_top:
                continue

            bars.append({
                "id": shift.id,
                "date": shift.work_date,
                "start": time_text(shift.start_time),
                "end": time_text(shift.end_time, end=True),
                "top": round(visible_top, 2),
                "height": round(
                    visible_bottom - visible_top, 2
                ),
            })

        chart_staffs.append({
            "staff": staff,
            "color": staff.color or "#b9d7f5",
            "bars": bars,
            "sort_start": min(
                (
                    minute(shift.start_time)
                    for shift in staff_shifts
                    if shift.start_time is not None
                ),
                default=9999,
            ),
            "sort_order": min(
                (shift.id for shift in staff_shifts),
                default=999999,
            ),
        })

    chart_staffs.sort(
        key=lambda item: (
            item["sort_start"],
            item["sort_order"],
        )
    )
    from .staff_labels import prepare_chart_staffs

    chart_staffs = prepare_chart_staffs(
        chart_staffs,
        day_shifts,
        selected_date,
        stores,
        selected_store,
    )
    # 不足日の表示
    shortages = ShiftShortage.objects.filter(
        store__in=editable_stores,
        target_date__year=year,
        target_date__month=month,
        assigned_count__lt=F("required_count"),
    )

    if selected_store:
        shortages = shortages.filter(
            store=selected_store
        )

    shortage_days = set(
        shortages.values_list(
            "target_date__day", flat=True
        )
    )

    day_shortages = list(
        shortages.filter(
            target_date=selected_date
        ).select_related("store", "shift_time_slot")
    )

    for shortage in day_shortages:
        shortage.missing_count = (
            shortage.required_count - shortage.assigned_count
        )

    # 希望一覧
    memberships = StaffStoreAssignment.objects.filter(
        store__in=editable_stores,
        starts_on__lte=selected_date,
    ).filter(
        Q(ends_on__isnull=True)
        | Q(ends_on__gte=selected_date)
    )

    if selected_store:
        memberships = memberships.filter(
            store=selected_store
        )

    visible_staff_ids = set(
        memberships.values_list("staff_id", flat=True)
    )

    preferences = {
        preference.submission.staff_id: preference
        for preference in ShiftPreference.objects.filter(
            submission__is_latest=True,
            submission__year=year,
            submission__month=month,
            target_date=selected_date,
        ).select_related(
            "submission__staff"
        ).order_by("id")
    }

    preference_rows = []

    for staff in staffs:
        if staff.id not in visible_staff_ids or not staff.is_active:
            continue

        preference = preferences.get(staff.id)
        assigned = [
            shift for shift in day_shifts
            if shift.staff_id == staff.id
        ]

        wish_text = "未提出"
        status = "未提出"

        if preference:
            if not preference.is_available:
                wish_text = "希望休"
                status = "相違あり" if assigned else "希望休"

            elif (
                preference.available_start_time is None
                or preference.available_end_time is None
            ):
                wish_text = "出勤可能（時刻未設定）"
                status = "要確認"

            else:
                wish_text = (
                    time_text(preference.available_start_time)
                    + "〜"
                    + time_text(
                        preference.available_end_time,
                        end=True,
                    )
                )
                status = "未配置"

                if assigned:
                    matches = all(
                        shift.start_time is not None
                        and shift.end_time is not None
                        and minute(shift.start_time)
                        >= minute(preference.available_start_time)
                        and minute(shift.end_time, end=True)
                        <= minute(
                            preference.available_end_time,
                            end=True,
                        )
                        for shift in assigned
                    )
                    status = (
                        "希望どおり" if matches else "相違あり"
                    )

        current_assignments = []

        for shift in assigned:
            if shift.start_time is None or shift.end_time is None:
                continue

            slot_name = (
                shift.shift_time_slot.name
                if shift.shift_time_slot
                else "手動枠"
            )

            current_assignments.append(
                f"{shift.store.name}・{slot_name} "
                f"{time_text(shift.start_time)}"
                f"〜{time_text(shift.end_time, end=True)}"
            )

        preference_rows.append({
            "staff": staff,
            "wish_text": wish_text,
            "status": status,
            "current_assignments": current_assignments,
        })

    # 下書きの月だけ編集可能
    states = list(
        ShiftSchedule.objects.filter(
            area_id__in=editable_stores.values("area_id"),
            year=year,
            month=month,
        )
    )

    if selected_store:
        states = [
            state for state in states
            if state.area_id == selected_store.area_id
        ]

    can_edit_shifts = any(
        state.status == 2 for state in states
    )

    if selected_shift:
        state = next(
            (
                state for state in states
                if state.area_id == selected_shift.store.area_id
            ),
            None,
        )
        can_edit_shifts = (
            selected_shift.status == 1
            and state is not None
            and state.status == 2
        )

    # 前月・翌月
    prev_month_date = (
        selected_date.replace(day=1)
        - timedelta(days=1)
    ).replace(day=1)

    next_month_date = (
        selected_date.replace(day=1)
        + timedelta(days=32)
    ).replace(day=1)

    context = get_calendar_data(year, month, day)

    context.update({
        "staff_name": manager.name,
        "year": year,
        "month": month,
        "display_day": day,
        "staffs": staffs,
        "stores": stores,
        "timeslots": ShiftTimeSlot.objects.filter(
            store__in=editable_stores
        ),
        "form": form,
        "selected_shift": selected_shift,
        "selected_store_id": selected_store_id,
        "prev_month_date": prev_month_date,
        "next_month_date": next_month_date,
        "chart_hours": chart_hours,
        "chart_staffs": chart_staffs,
        "chart_height": chart_height,
        "day_shortages": day_shortages,
        "can_edit_shifts": can_edit_shifts,
        "preference_rows": preference_rows,
        "selected_weekday": [
            "月", "火", "水", "木", "金", "土", "日"
        ][selected_date.weekday()],
        "staff_departure_times": {
            str(staff.id): staff.last_train_departure_time.strftime("%H:%M")
            for staff in staffs
            if staff.last_train_departure_time is not None
        },
        "existing_shift_pairs": [
            {
                "staff_id": row["staff_id"],
                "date": row["work_date"].isoformat(),
            }
            for row in ShiftAssignment.objects.filter(
                store__in=editable_stores,
                status__in=[1, 2],
            ).values(
                "staff_id", "work_date"
            ).distinct()
        ],
    })

    context["calendar_weeks"] = [
        [
            {
                "day": number,
                "shortage": number in shortage_days,
            }
            for number in week
        ]
        for week in context["weeks"]
    ]

    return render(
        request,
        "shift_management/edit_shift.html",
        context,
    )
def staff_login(request):
    """スタッフログイン画面"""

    if request.method == "POST":
        form = StaffLoginForm(request.POST)

        if form.is_valid():
            username = form.cleaned_data["username"]
            password = form.cleaned_data["password"]
            user = authenticate(
                request,
                username=username,
                password=password
            )
            if user is not None:
                staff = user.staffprofile
                if staff.role == 1:
                    login(request,user)
                    request.session["deferred_request_ids"] = []
                    return redirect("shift_management:staff_main")
                else:
                    messages.error(request,"管理者ログインからログインしてください")
                    return redirect("shift_management:manager_login")
            else:
                messages.error(request,"従業員番号またはパスワードが違います")
    else:
        form = StaffLoginForm()

    return render(
        request,
        "shift_management/staff_login.html",
        {"form": form}
    )

def manager_login(request):
    """管理者ログイン"""
    if request.method == "POST":
        form = ManagerLoginForm(request.POST)

        if form.is_valid():
            username = form.cleaned_data["username"]
            password = form.cleaned_data["password"]
            area_code = form.cleaned_data["area_code"]
            user = authenticate(
                request,
                username=username,
                password=password,
            )
            if user is not None:
                staff = user.staffprofile
                if staff.role  == 2 :
                    has_permission =ManagementScope.objects.filter(
                        manager = staff,
                        area__code = area_code
                    ).exists()

                    if has_permission:
                        login(request,user)
                        request.session["deferred_request_ids"] = []
                        return redirect("shift_management:manager_main")
                    else:
                        messages.error(request,"エリアコードが一致しません")
                else:
                    messages.error(request,"従業員ログインからログインしてください")
                    return redirect("shift_management:staff_login")
            else:
                messages.error(request,"従業員番号またはパスワードが違います")
    else:
        form = ManagerLoginForm()

    return render(
        request,
        "shift_management/manager_login.html",
        {"form": form}
    )

@manager_required
def all_staff(request):
    """従業員一覧"""
    staffs = StaffProfile.objects.filter(is_active=True)


    context = {
        'staffs': staffs,
    }


    return render(
        request,"shift_management/all_staff.html",context
    )

@manager_required
def add_staff(request):
    """従業員追加"""
    if request.method == "POST":
        form = StaffProfileForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data["username"]
               
            user = User.objects.create_user(
                username=username,
                password=form.cleaned_data["password"]
            )
                #ユーザーに関連付ける
            staff = form.save(commit=False)
            staff.user = user
            staff.save()
            return redirect("shift_management:all_staff")
    else:
        form = StaffProfileForm()

    return render(
        request,
        "shift_management/add_staff.html",
        {"form": form}
    )

@manager_required
def edit_staff(request, staff_id):
    """従業員編集"""
    staff = StaffProfile.objects.get(id=staff_id)
 
    if request.method == "POST":
            action = request.POST.get("action")
            if action == "retire":
                staff.is_active = False
                staff.save()
                return redirect("shift_management:all_staff")
         

            form = StaffEditForm(request.POST,instance=staff)
            if form.is_valid():
                staff = form.save()
                return redirect("shift_management:all_staff")
    else:
            form = StaffEditForm(instance=staff)

    context = {
        'staff': staff,
        'form':form,
    }

    return render(
        request,
        'shift_management/edit_staff.html',
        context
    )

#ログアウト機能
@require_POST
def logout_view(request):
    # ログイン画面へ前の操作の通知を持ち越さない
    for _ in messages.get_messages(request):
        pass

    logout(request)
    return redirect("shift_management:staff_login")

#シフト自動生成
from collections import defaultdict
from jpholiday import JPHoliday


def _shift_minutes(value, end=False):
    result = value.hour * 60 + value.minute
    return 1440 if end and result == 0 else result


def _shift_time(value):
    return time(0, 0) if value == 1440 else time(
        value // 60, value % 60
    )


@manager_required
@require_POST
@reject_past_generation
def generate_shift(request):
    """選択したエリア・月のシフトを下書きで生成する。"""
    try:
        year = int(request.POST["year"])
        month = int(request.POST["month"])
        area_id = int(request.POST["area"])

        if not 2000 <= year <= 2100:
            raise ValueError

        month_start = date(year, month, 1)
        month_end = date(
            year,
            month,
            calendar.monthrange(year, month)[1],
        )
    except (KeyError, ValueError, TypeError):
        messages.error(
            request,
            "作成する年月とエリアを選択してください。",
        )
        return redirect(
            "shift_management:shift_management"
        )

    destination = (
        reverse("shift_management:shift_management")
        + f"?year={year}&month={month}&area={area_id}"
    )

    def minute(value, end=False):
        result = value.hour * 60 + value.minute
        return 1440 if end and result == 0 else result

    def clock(value):
        if value == 1440:
            return time(0, 0)
        return time(value // 60, value % 60)

    try:
        with transaction.atomic():
            manager = StaffProfile.objects.get(
                user=request.user
            )

            area = get_object_or_404(
                Area, id=area_id
            )
            Area.objects.select_for_update().get(
                pk=area.pk
            )

            stores = list(
                controlled_stores(
                    manager, area
                ).select_for_update()
            )
            store_ids = [
                store.id for store in stores
            ]

            schedule = get_month_schedule(
                area, year, month
            )

            if schedule and schedule.status >= 2:
                raise ValidationError(
                    "作成済みです。下書きを削除してから再作成してください。"
                )

            if ShiftAssignment.objects.filter(
                store_id__in=store_ids,
                work_date__range=(month_start, month_end),
                status__in=[1, 2],
            ).exists():
                raise ValidationError(
                    "既存シフトがあります。"
                    "管理ページで下書きを削除してから作成してください。"
                    "確定済みのシフトは削除できません。"
                )

            slots = list(
                ShiftTimeSlot.objects.filter(
                    store_id__in=store_ids,
                    is_active=True,
                ).select_related(
                    "store"
                ).order_by("id")
            )

            if not slots:
                raise ValidationError(
                    "有効なシフト枠がありません。"
                )

            for slot in slots:
                if (
                    minute(slot.end_time, end=True)
                    <= minute(slot.start_time)
                ):
                    raise ValidationError(
                        f"{slot.store.name}・{slot.name}："
                        "24:00を超える深夜勤務は今回の対象外です。"
                    )

            # 今回の対象エリア・月の不足記録を作り直す
            ShiftShortage.objects.filter(
                store_id__in=store_ids,
                target_date__range=(
                    month_start, month_end
                ),
            ).delete()

            preferences = list(
                ShiftPreference.objects.filter(
                    submission__is_latest=True,
                    submission__year=year,
                    submission__month=month,
                    submission__status__in=[2, 3],
                    submission__staff__is_active=True,
                    submission__staff__is_trainee=False,
                    target_date__range=(
                        month_start, month_end
                    ),
                    is_available=True,
                ).select_related(
                    "submission__staff"
                )
            )

            preferences_by_day = defaultdict(list)
            seen = set()

            for preference in preferences:
                key = (
                    preference.submission.staff_id,
                    preference.target_date,
                )

                if key in seen:
                    raise ValidationError(
                        "同じスタッフ・日付の最新希望が重複しています。"
                    )

                seen.add(key)
                preferences_by_day[
                    preference.target_date
                ].append(preference)

            memberships = list(
                StaffStoreAssignment.objects.filter(
                    store_id__in=store_ids,
                    starts_on__lte=month_end,
                ).filter(
                    Q(ends_on__isnull=True)
                    | Q(ends_on__gte=month_start)
                )
            )

            from .evaluation_services import generation_evaluation_scores

            candidate_ids = {
                preference.submission.staff_id
                for preference in preferences
            } & {
                membership.staff_id
                for membership in memberships
            }

            evaluation_scores = generation_evaluation_scores(
                area,
                candidate_ids,
                year,
                month,
            )
            working_days = defaultdict(set)
            weekly_minutes = defaultdict(int)
            monthly_minutes = defaultdict(int)

            # 月初の週全体と、月末の連勤を確認
            history_start = month_start - timedelta(
                days=max(
                    5, month_start.weekday()
                )
            )
            history_end = month_end + timedelta(
                days=5
            )

            history = ShiftAssignment.objects.filter(
                work_date__range=(
                    history_start, history_end
                ),
                status__in=[1, 2],
                staff__isnull=False,
            ).select_related("shift_time_slot")

            for shift in history:
                working_days[shift.staff_id].add(
                    shift.work_date
                )

                start = shift.start_time
                end = shift.end_time

                if shift.shift_time_slot:
                    if start is None:
                        start = shift.shift_time_slot.start_time
                    if end is None:
                        end = shift.shift_time_slot.end_time

                if start is None or end is None:
                    raise ValidationError(
                        f"既存シフトID {shift.id} の勤務時刻が未設定です。"
                    )

                duration = (
                    minute(end, end=True)
                    - minute(start)
                )

                if duration <= 0:
                    raise ValidationError(
                        f"既存シフトID {shift.id}："
                        "24:00を超える勤務は今回の対象外です。"
                    )

                duration = max(
                    0,
                    duration - shift.break_minutes,
                )

                week = shift.work_date - timedelta(
                    days=shift.work_date.weekday()
                )

                weekly_minutes[
                    shift.staff_id, week
                ] += duration

                if month_start <= shift.work_date <= month_end:
                    monthly_minutes[
                        shift.staff_id
                    ] += duration

            def candidates_for(day, slot):
                result = []
                start = minute(slot.start_time)
                end = minute(
                    slot.end_time, end=True
                )
                week = day - timedelta(
                    days=day.weekday()
                )
                week_end = week + timedelta(days=6)

                member_ids = {
                    item.staff_id
                    for item in memberships
                    if item.store_id == slot.store_id
                    and item.starts_on <= day
                    and (
                        item.ends_on is None
                        or item.ends_on >= day
                    )
                }

                for preference in preferences_by_day[day]:
                    staff = preference.submission.staff
                    days = working_days[staff.id]

                    if staff.id not in member_ids:
                        continue

                    if staff.joined_on > day or day in days:
                        continue


                    actual_end = end

                    if staff.last_train_departure_time is not None:
                        actual_end = min(
                            end,
                            minute(
                                staff.last_train_departure_time,
                                end=True,
                            ),
                        )

                    if (
                        actual_end <= start
                        or end - actual_end > 15
                    ):
                        continue

                    if (
                        (
                            preference.available_start_time is not None
                            and minute(preference.available_start_time) > start
                        )
                        or (
                            preference.available_end_time is not None
                            and minute(
                                preference.available_end_time,
                                end=True,
                            ) < actual_end
                        )
                    ):
                        continue

                    duration = actual_end - start

                    if (
                        staff.weekly_max_minutes is not None
                        and weekly_minutes[staff.id, week] + duration
                        > staff.weekly_max_minutes
                    ):
                        continue

                    if (
                        staff.monthly_max_minutes is not None
                        and monthly_minutes[staff.id] + duration
                        > staff.monthly_max_minutes
                    ):
                        continue

                    if sum(
                        week <= value <= week_end
                        for value in days
                    ) >= 5:
                        continue

                    consecutive = 1

                    for direction in (-1, 1):
                        check = day + timedelta(
                            days=direction
                        )
                        while check in days:
                            consecutive += 1
                            check += timedelta(
                                days=direction
                            )

                    if consecutive > 5:
                        continue

                    result.append(
                        (staff, actual_end, duration)
                    )

                    result.sort(
                        key=lambda item: (
                            not item[0].shift_priority,
                            monthly_minutes[item[0].id],
                            -evaluation_scores.get(item[0].id, 3),
                            item[0].id,
                        )
                    )
                return result

            if schedule is None:
                schedule = ShiftSchedule.objects.create(
                    area=area,
                    year=year,
                    month=month,
                    status=1,
                    created_by=manager,
                )

            generated = 0
            shortage_slots = 0
            shortage_people = 0

            for number in range(
                1, month_end.day + 1
            ):
                day = date(
                    year, month, number
                )
                week = day - timedelta(
                    days=day.weekday()
                )

                # 候補が少ない枠を先に配置
                daily_slots = sorted(
                    slots,
                    key=lambda slot: (
                        len(candidates_for(day, slot))
                        - staffing_count(day, slot),
                        -minute(
                            slot.end_time, end=True
                        ),
                        slot.id,
                    ),
                )

                for slot in daily_slots:
                    required = staffing_count(
                        day, slot
                    )

                    if required == 0:
                        continue

                    candidates = candidates_for(
                        day, slot
                    )
                    end = minute(
                        slot.end_time, end=True
                    )

                    full = [
                        item for item in candidates
                        if item[1] == end
                    ]
                    early = [
                        item for item in candidates
                        if item[1] < end
                    ]

                    selected = full[:required]

                    # 終了まで残る人がいる場合だけ早退者を追加
                    if selected:
                        selected.extend(
                            early[
                                :required - len(selected)
                            ]
                        )

                    for staff, actual_end, duration in selected:
                        ShiftAssignment.objects.create(
                            schedule=schedule,
                            staff=staff,
                            store=slot.store,
                            work_date=day,
                            shift_time_slot=slot,
                            start_time=slot.start_time,
                            end_time=clock(actual_end),
                            status=1,
                            is_training=False,
                            created_by=manager,
                        )

                        working_days[staff.id].add(day)
                        weekly_minutes[
                            staff.id, week
                        ] += duration
                        monthly_minutes[
                            staff.id
                        ] += duration
                        generated += 1

                    missing = (
                        required - len(selected)
                    )

                    if missing:
                        ShiftShortage.objects.create(
                            schedule=schedule,
                            store=slot.store,
                            target_date=day,
                            shift_time_slot=slot,
                            required_count=required,
                            assigned_count=len(selected),
                            note=(
                                "自動生成：希望・退勤・"
                                "勤務上限などで配置不足"
                            ),
                        )

                        shortage_slots += 1
                        shortage_people += missing

            # 配置0件でも「生成済み」にする
            schedule.status = 2
            schedule.generated_at = timezone.now()
            schedule.published_at = None
            schedule.save(
                update_fields=[
                    "status",
                    "generated_at",
                    "published_at",
                ]
            )

    except (ValueError, ValidationError) as exc:
        text = (
            " ".join(exc.messages)
            if isinstance(exc, ValidationError)
            else str(exc)
        )
        messages.error(request, text)
        return redirect(destination)

    messages.success(
        request,
        f"{year}年{month}月：下書き{generated}件を作成しました。"
        f"不足{shortage_slots}枠・延べ{shortage_people}人です。",
    )

    return redirect(destination)


def message(request):
        return render(
        request,
        'shift_management/message.html',
    )

@login_required(login_url="shift_management:manager_login")
def evaluation_list(request):
    """前月分の評価シート一覧"""

    evaluator = get_object_or_404(
        StaffProfile,
        user=request.user
    )

    if evaluator.role != 2 and not evaluator.is_position_holder:
        raise PermissionDenied

    today = timezone.localdate()
    previous_month = today.replace(day=1) - timedelta(days=1)

    year = previous_month.year
    month = previous_month.month

    scopes = ManagementScope.objects.filter(
        manager=evaluator
    )

    areas = get_evaluation_areas(
        evaluator=evaluator,
        year=year,
        month=month
    )

    evaluation_rows = []

    for area in areas:
        setting = EvaluationSetting.objects.filter(
            area=area,
            is_enabled=True
        ).first()

        if setting is None:
            continue

        targets = get_evaluation_targets(
            evaluator=evaluator,
            year=year,
            month=month,
            area=area
        )

        for staff in targets:
            evaluation = StaffEvaluation.objects.filter(
                area=area,
                evaluator=evaluator,
                staff=staff,
                year=year,
                month=month
            ).first()

            evaluation_rows.append({
                "area": area,
                "staff": staff,
                "submitted": (
                    evaluation is not None
                    and evaluation.submitted_at is not None
                ),
            })

    return render(
        request,
        "shift_management/evaluation_list.html",
        {
            "year": year,
            "month": month,
            "evaluation_rows": evaluation_rows,
            "staff_name": evaluator.name,
        }
    )

@manager_required
def evaluation_settings(request):
    """管轄エリアの評価制度設定"""

    manager = request.user.staffprofile

    scopes = ManagementScope.objects.filter(
        manager=manager
    )

    area_ids = set()

    for scope in scopes:
        if scope.area_id:
            area_ids.add(scope.area_id)

        if scope.store_id:
            area_ids.add(scope.store.area_id)

    areas = Area.objects.filter(
        id__in=area_ids
    ).order_by("name")

    if request.method == "POST":
        area_id = request.POST.get("area_id")

        area = get_object_or_404(
            areas,
            pk=area_id
        )

        is_enabled = request.POST.get("is_enabled") == "on"

        try:
            deadline_day = int(
                request.POST.get("deadline_day", "15")
            )
        except (ValueError, TypeError):
            messages.error(request, "提出期限を正しく入力してください。")
            return redirect("shift_management:evaluation_settings")

        if not 1 <= deadline_day <= 28:
            messages.error(
                request,
                "提出期限は1日から28日までで指定してください。"
            )
            return redirect("shift_management:evaluation_settings")

        EvaluationSetting.objects.update_or_create(
            area=area,
            defaults={
                "is_enabled": is_enabled,
                "deadline_day": deadline_day,
            }
        )

        messages.success(
            request,
            f"{area.name}の評価設定を保存しました。"
        )

        return redirect("shift_management:evaluation_settings")

    settings_rows = []

    for area in areas:
        setting, created = EvaluationSetting.objects.get_or_create(
            area=area
        )

        settings_rows.append({
            "area": area,
            "setting": setting,
        })

    return render(
        request,
        "shift_management/evaluation_settings.html",
        {
            "settings_rows": settings_rows,
            "staff_name": manager.name,
        }
    )
@manager_required
def system_settings(request):
    """システム全体の設定トップページ"""

    return render(
        request,
        "shift_management/system_settings.html"
    )

@manager_required
def evaluation_items(request, area_id):
    """エリア独自の評価項目管理"""

    manager = request.user.staffprofile

    scopes = ManagementScope.objects.filter(
        manager=manager
    )

    allowed_area_ids = set()

    for scope in scopes:
        if scope.area_id:
            allowed_area_ids.add(scope.area_id)

        if scope.store_id:
            allowed_area_ids.add(scope.store.area_id)

    area = get_object_or_404(
        Area,
        id=area_id,
        id__in=allowed_area_ids
    )

    if request.method == "POST":
        item_id = request.POST.get("item_id")
        name = request.POST.get("name", "").strip()
        description = request.POST.get(
            "description", ""
        ).strip()

        try:
            weight = int(request.POST.get("weight", "1"))
        except (ValueError, TypeError):
            messages.error(request, "配点を正しく入力してください。")
            return redirect(
                "shift_management:evaluation_items",
                area_id=area.id
            )

        if not name or len(name) > 100 or weight not in (1, 2, 3):
            messages.error(
                request,
                "項目名と配点を正しく入力してください。"
            )
            return redirect(
                "shift_management:evaluation_items",
                area_id=area.id
            )

        if item_id:
            item = get_object_or_404(
                EvaluationItem,
                id=item_id,
                area=area
            )
        else:
            item = EvaluationItem(area=area)

        item.name = name
        item.description = description
        item.weight = weight
        item.save()

        messages.success(request, "評価項目を保存しました。")

        return redirect(
            "shift_management:evaluation_items",
            area_id=area.id
        )

    common_items = EvaluationItem.objects.filter(
        area__isnull=True,
        is_active=True
    ).order_by("id")

    area_items = EvaluationItem.objects.filter(
        area=area,
        is_active=True
    ).order_by("id")

    return render(
        request,
        "shift_management/evaluation_items.html",
        {
            "area": area,
            "common_items": common_items,
            "area_items": area_items,
        }
    )
from django.core.exceptions import ValidationError
from .evaluation_services import submit_evaluation


@login_required(login_url="shift_management:manager_login")
def evaluation_detail(request, area_id, staff_id):
    """前月分のスタッフ評価入力・提出画面"""

    evaluator = get_object_or_404(
        StaffProfile,
        user=request.user
    )

    if evaluator.role != 2 and not evaluator.is_position_holder:
        raise PermissionDenied

    today = timezone.localdate()
    previous_month = today.replace(day=1) - timedelta(days=1)

    year = previous_month.year
    month = previous_month.month

    allowed_areas = get_evaluation_areas(
        evaluator=evaluator,
        year=year,
        month=month
    )

    area = get_object_or_404(
        allowed_areas,
        pk=area_id
    )

    setting = get_object_or_404(
        EvaluationSetting,
        area=area,
        is_enabled=True
    )

    # 評価対象者をサーバー側でも再確認
    targets = get_evaluation_targets(
        evaluator=evaluator,
        year=year,
        month=month,
        area=area
    )

    staff = get_object_or_404(
        targets,
        pk=staff_id
    )

    evaluation = StaffEvaluation.objects.filter(
        area=area,
        evaluator=evaluator,
        staff=staff,
        year=year,
        month=month
    ).first()

    items = EvaluationItem.objects.filter(
        Q(area__isnull=True) | Q(area=area),
        is_active=True
    ).order_by("id")

    if request.method == "POST":

        if evaluation and evaluation.submitted_at:
            messages.error(request, "この評価は提出済みです。")
            return redirect(
                "shift_management:evaluation_detail",
                area_id=area.id,
                staff_id=staff.id
            )

        # 毎月の提出期限を確認
        #if today.day > setting.deadline_day:
            #messages.error(request, "今月の評価提出期限を過ぎています。")
            #return redirect(
                #"shift_management:evaluation_detail",
                #area_id=area.id,
                #staff_id=staff.id
            #)

        item_scores = {}

        try:
            for item in items:
                item_scores[item.id] = int(
                    request.POST.get(f"score_{item.id}", "")
                )
        except (TypeError, ValueError):
            messages.error(request, "すべての項目を採点してください。")
            return redirect(
                "shift_management:evaluation_detail",
                area_id=area.id,
                staff_id=staff.id
            )

        if evaluation is None:
            evaluation = StaffEvaluation.objects.create(
                area=area,
                evaluator=evaluator,
                staff=staff,
                year=year,
                month=month
            )

        try:
            total_score = submit_evaluation(
                evaluation=evaluation,
                item_scores=item_scores,
                comment=request.POST.get("comment", "")
            )
        except ValidationError as exc:
            messages.error(
                request,
                " ".join(exc.messages)
            )
            return redirect(
                "shift_management:evaluation_detail",
                area_id=area.id,
                staff_id=staff.id
            )

        messages.success(
            request,
            f"{staff.name}さんの評価を提出しました。（{total_score}点）"
        )

        return redirect("shift_management:evaluation_list")

    saved_scores = {}

    if evaluation:
        saved_scores = {
            score.item_id: score.score
            for score in evaluation.scores.all()
        }

    return render(
        request,
        "shift_management/evaluation_detail.html",
        {
            "area": area,
            "staff": staff,
            "year": year,
            "month": month,
            "items": items,
            "evaluation": evaluation,
            "saved_scores": saved_scores,
            "deadline_day": setting.deadline_day,
            #"deadline_passed": today.day > setting.deadline_day,
            #評価を一時的に期限解除
            "deadline_passed": False,
        }
    )
def csrf_failure(request, reason=""):
    if request.user.is_authenticated:
        staff = getattr(request.user, "staffprofile", None)

        if staff and staff.role == 2:
            return_url = reverse("shift_management:manager_main")
        elif staff and staff.role == 1:
            return_url = reverse("shift_management:staff_main")
        else:
            return_url = reverse("shift_management:staff_login")
    else:
        return_url = reverse("shift_management:staff_login")

    return render(
        request,
        "shift_management/csrf_failure.html",
        {"return_url": return_url},
        status=403,
    )
@manager_required
def manager_main(request):
    context = get_main_shift_context(request)

    return render(
        request,
        "shift_management/manager_main.html",
        context,
    )