from contextlib import contextmanager
from datetime import datetime, time

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from .decorators import manager_required
from .models import (
    Area,
    StaffProfile,
    ShiftAssignment,
    ShiftAdjustmentRequest,
    MessageThread,
    Message,
)
from .shift_controls import allowed_stores, get_month_schedule
from .staff_requests import eligibility, recalculate_shortages


def same_slot(shortage):
    return ShiftAdjustmentRequest.objects.filter(
        shortage__store_id=shortage.store_id,
        shortage__target_date=shortage.target_date,
        shortage__shift_time_slot_id=shortage.shift_time_slot_id,
    )


def reserved_count(shortage):
    # 同じ人への重複した依頼を人数として重ねて数えない
    return (
        same_slot(shortage)
        .filter(status=2, recipient__isnull=False)
        .values("recipient_id")
        .distinct()
        .count()
    )


def send_event(sender, recipient, body):
    if sender is None or recipient is None:
        return

    thread = (
        MessageThread.objects.filter(
            created_by=sender,
            recipient=recipient,
        )
        .order_by("pk")
        .first()
    )

    if thread is None:
        thread = (
            MessageThread.objects.filter(
                created_by=recipient,
                recipient=sender,
            )
            .order_by("pk")
            .first()
        )

    if thread is None:
        thread = MessageThread.objects.create(
            created_by=sender,
            recipient=recipient,
        )

    Message.objects.create(
        thread=thread,
        sender=sender,
        body=body,
    )


@contextmanager
def locked_request(request_id):
    # エリア→スタッフ→依頼の順でロックする
    snapshot = get_object_or_404(
        ShiftAdjustmentRequest.objects.select_related(
            "shortage__store"
        ),
        pk=request_id,
    )

    store = snapshot.shortage.store
    if store is None or store.area_id is None:
        raise ValidationError("対象店舗・エリアがありません。")

    with transaction.atomic():
        Area.objects.select_for_update().get(pk=store.area_id)

        list(
            StaffProfile.objects.select_for_update()
            .filter(
                pk__in=[
                    value
                    for value in (
                        snapshot.recipient_id,
                        snapshot.requested_by_id,
                    )
                    if value is not None
                ]
            )
            .order_by("pk")
        )

        row = get_object_or_404(
            ShiftAdjustmentRequest.objects.select_for_update(),
            pk=request_id,
        )

        if row.shortage_id != snapshot.shortage_id:
            raise ValidationError("依頼が変更されました。開き直してください。")

        yield row


def check_shortage(shortage):
    if (
        shortage.store is None
        or shortage.shift_time_slot is None
        or not shortage.shift_time_slot.is_active
    ):
        raise ValidationError("対象の店舗・シフト枠は利用できません。")

    schedule = get_month_schedule(
        shortage.store.area,
        shortage.target_date.year,
        shortage.target_date.month,
    )

    if schedule is None or schedule.status != 2:
        raise ValidationError(
            "自動生成済みの下書きシフトでのみ操作できます。"
        )

    starts_at = timezone.make_aware(
        datetime.combine(
            shortage.target_date,
            shortage.shift_time_slot.start_time,
        )
    )

    if starts_at <= timezone.now():
        raise ValidationError("勤務開始時刻を過ぎています。")

    recalculate_shortages(
        [shortage.store],
        shortage.target_date,
    )
    shortage.refresh_from_db()

    return schedule


def close_remaining(shortage):
    if (
        shortage.assigned_count + reserved_count(shortage)
        >= shortage.required_count
    ):
        same_slot(shortage).filter(status=1).update(status=5)


def save_response(staff, request_id, action, note=""):
    with locked_request(request_id) as row:
        if row.recipient_id != staff.pk:
            raise ValidationError("この依頼には回答できません。")

        if (
            row.status != 1
            or row.response_deadline <= timezone.now()
        ):
            raise ValidationError(
                "回答済み・受付終了、または回答期限を過ぎています。"
            )

        if action not in ("accept", "decline"):
            raise ValidationError("操作が不正です。")

        if action == "accept":
            shortage = row.shortage
            check_shortage(shortage)

            if (
                shortage.assigned_count + reserved_count(shortage)
                >= shortage.required_count
            ):
                raise ValidationError(
                    "必要人数分の承認が既に集まっています。"
                )

            # 未反映の承認も、同日の勤務予定として扱う
            if ShiftAdjustmentRequest.objects.filter(
                recipient=staff,
                shortage__target_date=shortage.target_date,
                status=2,
            ).exists():
                raise ValidationError(
                    "同日に、別の反映待ちの出勤依頼があります。"
                )

            _, reason = eligibility(staff, shortage)
            if reason:
                raise ValidationError(f"現在は承認できません：{reason}")

        row.status = 2 if action == "accept" else 3
        row.response_note = note.strip()[:2000]
        row.responded_at = timezone.now()
        row.save(
            update_fields=[
                "status",
                "response_note",
                "responded_at",
            ]
        )

        shortage = row.shortage
        result = "承認" if action == "accept" else "辞退"
        body = (
            f"{staff.name} {staff.firstname or ''}さんが"
            f"出勤依頼を{result}しました。\n"
            f"{shortage.target_date:%Y/%m/%d} "
            f"{shortage.store.name if shortage.store else ''}\n"
            f"{shortage.shift_time_slot.name if shortage.shift_time_slot else ''}"
        )

        if action == "accept":
            body += "\n出勤依頼カードの「シフトに反映」を押してください。"
            close_remaining(shortage)

        send_event(staff, row.requested_by, body)
        return row


@manager_required
@require_POST
def apply_request(request, request_id):
    manager = request.user.staffprofile
    row = get_object_or_404(
        ShiftAdjustmentRequest,
        pk=request_id,
        requested_by=manager,
    )
    recipient_id = row.recipient_id

    try:
        with locked_request(request_id) as row:
            if row.requested_by_id != manager.pk:
                raise ValidationError("この依頼は反映できません。")

            shortage = row.shortage

            if not allowed_stores(manager).filter(
                pk=shortage.store_id
            ).exists():
                raise ValidationError("対象店舗の管理権限がありません。")

            if row.status == 6:
                raise ValidationError("既にシフトへ反映済みです。")

            if row.status != 2 or row.recipient is None:
                raise ValidationError("承認済みの依頼だけ反映できます。")

            schedule = check_shortage(shortage)

            if shortage.assigned_count >= shortage.required_count:
                raise ValidationError(
                    "この枠は既に必要人数を満たしています。"
                )

            actual_end, reason = eligibility(
                row.recipient,
                shortage,
            )
            if reason:
                raise ValidationError(f"現在は反映できません：{reason}")

            end_time = (
                time(0, 0)
                if actual_end == 1440
                else time(actual_end // 60, actual_end % 60)
            )

            ShiftAssignment.objects.create(
                schedule=schedule,
                staff=row.recipient,
                store=shortage.store,
                work_date=shortage.target_date,
                shift_time_slot=shortage.shift_time_slot,
                start_time=shortage.shift_time_slot.start_time,
                end_time=end_time,
                break_minutes=0,
                status=1,
                is_training=False,
                created_by=manager,
                note=f"出勤依頼 #{row.pk} から反映",
            )

            row.status = 6
            row.save(update_fields=["status"])

            recalculate_shortages(
                [shortage.store],
                shortage.target_date,
            )
            shortage.refresh_from_db()
            close_remaining(shortage)

            send_event(
                manager,
                row.recipient,
                (
                    f"{shortage.target_date:%Y/%m/%d} "
                    f"{shortage.store.name}の出勤依頼を"
                    "下書きシフトに反映しました。\n"
                    "公開後にカレンダーで確認できます。"
                ),
            )

        messages.success(
            request,
            "下書きシフトに反映しました。勤務時間・休憩を確認してください。",
        )

    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))

    if recipient_id:
        from .messaging import contact_staff

        if contact_staff(manager).filter(pk=recipient_id).exists():
            return redirect(
                "shift_management:conversation",
                staff_id=recipient_id,
            )

    return redirect("shift_management:message")