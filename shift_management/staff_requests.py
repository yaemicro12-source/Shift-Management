from datetime import datetime, timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .decorators import manager_required
from .models import (
    Area,
    StaffProfile,
    StaffStoreAssignment,
    ShiftAssignment,
    ShiftTimeSlot,
    ShiftShortage,
    ShiftAdjustmentRequest,
    ShiftPreference,
)
from .shift_controls import (
    allowed_stores,
    get_month_schedule,
    required_count,
    generation_months,
)


def minute(value, end=False):
    result = value.hour * 60 + value.minute
    return 1440 if end and result == 0 else result


def interval(shift):
    start = shift.start_time
    end = shift.end_time

    if shift.shift_time_slot:
        if start is None:
            start = shift.shift_time_slot.start_time
        if end is None:
            end = shift.shift_time_slot.end_time

    if start is None or end is None:
        return None

    return minute(start), minute(end, end=True)


def reject_past_generation(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            year = int(request.POST["year"])
            month = int(request.POST["month"])
            datetime(year, month, 1)
        except (KeyError, ValueError, TypeError):
            messages.error(
                request,
                "対象年月を正しく選択してください。",
            )
            return redirect(
                "shift_management:shift_management"
            )

        allowed = {
            (item["year"], item["month"])
            for item in generation_months()
        }

        if (year, month) not in allowed:
            messages.error(
                request,
                "自動生成できるのは今月・来月・再来月だけです。",
            )
            return redirect(
                "shift_management:shift_management"
            )

        return view(request, *args, **kwargs)

    return wrapper


def recalculate_shortages(stores, day):
    """出勤依頼が消えないよう、不足記録のIDを維持して更新する。"""
    for store in stores:
        schedule = get_month_schedule(
            store.area, day.year, day.month
        )
        if schedule is None or schedule.status < 2:
            continue

        shifts = list(
            ShiftAssignment.objects.filter(
                store=store,
                work_date=day,
                status__in=[1, 2],
                is_training=False,
                staff__isnull=False,
                staff__is_trainee=False,
            ).select_related("shift_time_slot")
        )

        active_ids = []

        for slot in ShiftTimeSlot.objects.filter(
            store=store, is_active=True
        ):
            active_ids.append(slot.id)
            start = minute(slot.start_time)
            end = minute(slot.end_time, end=True)
            staff_ids = set()
            full_worker = False

            for shift in shifts:
                value = interval(shift)

                if (
                    value
                    and value[0] <= start
                    and value[1] > start
                    and value[1] >= end - 15
                ):
                    staff_ids.add(shift.staff_id)
                    full_worker = full_worker or value[1] >= end

            count = required_count(day, slot)
            assigned = len(staff_ids) if full_worker else 0

            existing = ShiftShortage.objects.filter(
                store=store,
                target_date=day,
                shift_time_slot=slot,
            )

            if existing.exists():
                existing.update(
                    required_count=count,
                    assigned_count=assigned,
                )
            elif assigned < count:
                ShiftShortage.objects.create(
                    schedule=schedule,
                    store=store,
                    target_date=day,
                    shift_time_slot=slot,
                    required_count=count,
                    assigned_count=assigned,
                )

        # 無効になった枠の依頼履歴も削除しない
        for old in ShiftShortage.objects.filter(
            store=store,
            target_date=day,
        ).exclude(shift_time_slot_id__in=active_ids):
            old.assigned_count = old.required_count
            old.save(update_fields=["assigned_count"])


def eligibility(staff, shortage):
    """希望外の出勤を依頼できるか。勤務希望自体は除外条件にしない。"""
    if not staff.is_active or not staff.user.is_active:
        return None, "在籍対象外またはログイン無効"

    if staff.is_trainee:
        return None, "研修中"

    day = shortage.target_date
    slot = shortage.shift_time_slot

    if not slot or not shortage.store:
        return None, "店舗・枠が未設定"

    if staff.joined_on > day:
        return None, "入社日前"

    belongs = StaffStoreAssignment.objects.filter(
        staff=staff,
        store=shortage.store,
        starts_on__lte=day,
    ).filter(
        Q(ends_on__isnull=True) | Q(ends_on__gte=day)
    ).exists()

    if not belongs:
        return None, "対象日の店舗所属なし"

    start = minute(slot.start_time)
    end = minute(slot.end_time, end=True)
    actual_end = end

    if staff.last_train_departure_time is not None:
        actual_end = min(
            end,
            minute(staff.last_train_departure_time, end=True),
        )

    if (
        end <= start
        or actual_end <= start
        or end - actual_end > 15
    ):
        return None, "退勤可能時刻が枠に合わない"

    week_start = day - timedelta(days=day.weekday())
    week_end = week_start + timedelta(days=6)
    month_first = day.replace(day=1)
    month_last = (
        month_first + timedelta(days=32)
    ).replace(day=1) - timedelta(days=1)

    history = list(
        ShiftAssignment.objects.filter(
            staff=staff,
            status__in=[1, 2],
            work_date__range=(
                min(
                    month_first,
                    week_start,
                    day - timedelta(days=5),
                ),
                max(
                    month_last,
                    week_end,
                    day + timedelta(days=5),
                ),
            ),
        ).select_related("shift_time_slot")
    )

    days = {shift.work_date for shift in history}

    if day in days:
        return None, "同日に勤務登録あり"

    if sum(
        week_start <= value <= week_end
        for value in days
    ) >= 5:
        return None, "週5日の上限"

    consecutive = 1
    for direction in (-1, 1):
        check = day + timedelta(days=direction)
        while check in days:
            consecutive += 1
            check += timedelta(days=direction)

    if consecutive > 5:
        return None, "6連勤以上になる"

    weekly = 0
    monthly = 0

    for shift in history:
        value = interval(shift)

        if value is None or value[1] <= value[0]:
            return None, "既存勤務時間を要確認"

        duration = max(
            0,
            value[1] - value[0] - shift.break_minutes,
        )

        if week_start <= shift.work_date <= week_end:
            weekly += duration

        if month_first <= shift.work_date <= month_last:
            monthly += duration

    duration = actual_end - start

    if (
        staff.weekly_max_minutes is not None
        and weekly + duration > staff.weekly_max_minutes
    ):
        return None, "週の勤務時間上限"

    if (
        staff.monthly_max_minutes is not None
        and monthly + duration > staff.monthly_max_minutes
    ):
        return None, "月の勤務時間上限"

    if actual_end < end:
        remaining = False

        for shift in ShiftAssignment.objects.filter(
            store=shortage.store,
            work_date=day,
            status__in=[1, 2],
        ).exclude(staff=staff).select_related("shift_time_slot"):
            value = interval(shift)

            if value and value[0] <= actual_end and value[1] >= end:
                remaining = True

        if not remaining:
            return None, "早退後に残るスタッフがいない"

    return actual_end, ""


@manager_required
def shortage_request(request, shortage_id):
    manager = request.user.staffprofile

    shortage = get_object_or_404(
        ShiftShortage.objects.select_related(
            "store__area", "shift_time_slot"
        ),
        pk=shortage_id,
        store__in=allowed_stores(manager),
    )

    schedule = get_month_schedule(
        shortage.store.area,
        shortage.target_date.year,
        shortage.target_date.month,
    )

    if (
        schedule is None
        or schedule.status != 2
        or not shortage.shift_time_slot
    ):
        messages.error(
            request,
            "出勤依頼は、自動生成済みの下書きの不足枠で利用できます。",
        )
        return redirect("shift_management:shift_management")

    start_at = timezone.make_aware(
        datetime.combine(
            shortage.target_date,
            shortage.shift_time_slot.start_time,
        )
    )

    if start_at <= timezone.now():
        messages.error(
            request,
            "勤務開始時刻を過ぎた枠には依頼できません。",
        )
        return redirect("shift_management:shift_management")

    if request.method == "POST":
        try:
            with transaction.atomic():
                Area.objects.select_for_update().get(
                    pk=shortage.store.area_id
                )

                state = get_month_schedule(
                    shortage.store.area,
                    shortage.target_date.year,
                    shortage.target_date.month,
                )

                if state is None or state.status != 2:
                    raise ValidationError(
                        "作成状態が変わりました。再確認してください。"
                    )

                recalculate_shortages(
                    [shortage.store], shortage.target_date
                )
                shortage.refresh_from_db()

                from .request_actions import reserved_count

                if (
                    shortage.assigned_count
                    + reserved_count(shortage)
                    >= shortage.required_count
                ):
                    raise ValidationError(
                        "必要人数分の承認が既に集まっています。"
                    )

                ids = {
                    int(value)
                    for value in request.POST.getlist("staff_ids")
                }

                if not ids:
                    raise ValidationError(
                        "送信先を選択してください。"
                    )

                recipients = list(
                    StaffProfile.objects.select_for_update().filter(
                        pk__in=ids
                    ).select_related("user")
                )

                if len(recipients) != len(ids):
                    raise ValidationError("送信先が不正です。")

                extra = request.POST.get("message", "").strip()

                if len(extra) > 2000:
                    raise ValidationError(
                        "補足は2000文字以内にしてください。"
                    )

                sent = 0
                skipped = 0

                for staff in recipients:
                    actual_end, reason = eligibility(staff, shortage)

                    if reason:
                        raise ValidationError(
                            f"{staff.name}：{reason}。"
                            "送信を取り消しました。"
                        )

                    pending = ShiftAdjustmentRequest.objects.filter(
                        shortage=shortage,
                        recipient=staff,
                        status=1,
                        response_deadline__gt=timezone.now(),
                    ).exists()

                    if pending:
                        skipped += 1
                        continue

                    end_text = (
                        "24:00"
                        if actual_end == 1440
                        else (
                            f"{actual_end // 60:02d}:"
                            f"{actual_end % 60:02d}"
                        )
                    )

                    text = (
                        f"{shortage.target_date:%Y/%m/%d} "
                        f"{shortage.store.name} "
                        f"{shortage.shift_time_slot.name}\n"
                        f"{shortage.shift_time_slot.start_time:%H:%M}"
                        f"〜{end_text}\n"
                        "人数不足のため、出勤可能かご回答ください。\n"
                        "勤務希望外のお願いです。"
                        "承認後、管理者が反映して勤務を確定します。"
                    )

                    if extra:
                        text += "\n\n" + extra

                    ShiftAdjustmentRequest.objects.create(
                        shortage=shortage,
                        recipient=staff,
                        requested_by=manager,
                        message=text,
                        status=1,
                        response_deadline=min(
                            timezone.now() + timedelta(hours=48),
                            start_at,
                        ),
                    )
                    sent += 1

            messages.success(
                request,
                f"{sent}人に出勤依頼を送信しました。"
                f"未回答の重複{skipped}人は送信していません。",
            )

        except (ValidationError, ValueError) as exc:
            messages.error(
                request,
                " ".join(exc.messages)
                if isinstance(exc, ValidationError)
                else "送信先が不正です。",
            )

        return redirect(
            "shift_management:shortage_request",
            shortage_id=shortage.id,
        )

    candidates = []
    excluded = []

    staffs = StaffProfile.objects.filter(
        staffstoreassignment__store=shortage.store
    ).distinct().select_related("user").order_by("id")

    for staff in staffs:
        actual_end, reason = eligibility(staff, shortage)

        preference = ShiftPreference.objects.filter(
            submission__staff=staff,
            submission__is_latest=True,
            submission__year=shortage.target_date.year,
            submission__month=shortage.target_date.month,
            target_date=shortage.target_date,
        ).order_by("-id").first()

        if preference is None:
            wish = "未提出"
        elif not preference.is_available:
            wish = "希望休"
        else:
            wish = "出勤希望あり（今回の依頼は別途確認）"

        if reason:
            excluded.append({
                "staff": staff,
                "reason": reason,
            })
            continue

        pending = ShiftAdjustmentRequest.objects.filter(
            shortage=shortage,
            recipient=staff,
            status=1,
            response_deadline__gt=timezone.now(),
        ).exists()

        end_text = (
            "24:00"
            if actual_end == 1440
            else f"{actual_end // 60:02d}:{actual_end % 60:02d}"
        )

        candidates.append({
            "staff": staff,
            "wish": wish,
            "pending": pending,
            "end": end_text,
        })

    return render(
        request,
        "shift_management/shortage_request.html",
        {
            "shortage": shortage,
            "candidates": candidates,
            "excluded": excluded,
        },
    )


@login_required(login_url="shift_management:staff_login")
def request_messages(request):
    staff = request.user.staffprofile

    if request.method == "POST":
        try:
            with transaction.atomic():
                row = get_object_or_404(
                    ShiftAdjustmentRequest.objects.select_for_update()
                    .select_related(
                        "shortage__store__area",
                        "shortage__shift_time_slot",
                    ),
                    pk=request.POST.get("request_id"),
                    recipient=staff,
                )

                if (
                    row.status != 1
                    or row.response_deadline <= timezone.now()
                ):
                    raise ValidationError(
                        "回答済み、または回答期限を過ぎています。"
                    )

                action = request.POST.get("action")

                if action not in ("accept", "decline"):
                    raise ValidationError("操作が不正です。")

                if action == "accept":
                    state = get_month_schedule(
                        row.shortage.store.area,
                        row.shortage.target_date.year,
                        row.shortage.target_date.month,
                    )
                    actual_end, reason = eligibility(
                        staff, row.shortage
                    )

                    if (
                        state is None
                        or state.status != 2
                        or reason
                    ):
                        raise ValidationError(
                            "現在は承諾できません。"
                            "勤務条件が変わった可能性があります。"
                        )

                row.status = 2 if action == "accept" else 3
                row.response_note = (
                    request.POST.get("note", "").strip()[:2000]
                )
                row.responded_at = timezone.now()
                row.save(
                    update_fields=[
                        "status",
                        "response_note",
                        "responded_at",
                    ]
                )

            messages.success(
                request,
                "回答を保存しました。勤務シフトはまだ変更されていません。",
            )

        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))

        return redirect("shift_management:message")

    received = list(
        ShiftAdjustmentRequest.objects.filter(
            recipient=staff
        ).select_related(
            "requested_by", "shortage__store"
        ).order_by("-requested_at")
    )

    for row in received:
        row.can_reply = (
            row.status == 1
            and row.response_deadline > timezone.now()
        )

    sent = ShiftAdjustmentRequest.objects.filter(
        requested_by=staff
    ).select_related(
        "recipient", "shortage__store"
    ).order_by("-requested_at")

    return render(
        request,
        "shift_management/request_messages.html",
        {
            "received": received,
            "sent": sent,
        },
    )