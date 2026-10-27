from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Area, StaffProfile, ShiftAdjustmentRequest
from .staff_requests import eligibility, recalculate_shortages
from .shift_controls import get_month_schedule


def pending_requests(request):
    if not request.user.is_authenticated:
        return {}

    staff = StaffProfile.objects.filter(
        user=request.user
    ).first()

    if staff is None or not staff.is_active:
        return {}

    rows = (
        ShiftAdjustmentRequest.objects.filter(
            recipient=staff,
            status=1,
            response_deadline__gt=timezone.now(),
        )
        .select_related(
            "requested_by",
            "shortage__store",
            "shortage__shift_time_slot",
        )
        .order_by("response_deadline", "pk")
    )

    deferred = request.session.get(
        "deferred_request_ids", []
    )

    return {
        "pending_request_count": rows.count(),
        "popup_requests": rows.exclude(pk__in=deferred),
    }


@login_required(login_url="shift_management:staff_login")
@require_POST
def respond_request(request, request_id):
    staff = get_object_or_404(
        StaffProfile,
        user=request.user,
        is_active=True,
    )

    try:
        with transaction.atomic():
            row = get_object_or_404(
                ShiftAdjustmentRequest.objects
                .select_for_update()
                .select_related(
                    "shortage__store__area",
                    "shortage__shift_time_slot",
                ),
                pk=request_id,
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

            if action == "hold":
                deferred = request.session.get(
                    "deferred_request_ids", []
                )

                request.session["deferred_request_ids"] = list(
                    set(deferred + [row.pk])
                )

                messages.info(
                    request,
                    "保留しました。次回ログイン時に再表示します。",
                )

            elif action in ("accept", "decline"):
                if action == "accept":
                    shortage = row.shortage

                    if (
                        not shortage.store
                        or not shortage.shift_time_slot
                        or not shortage.shift_time_slot.is_active
                    ):
                        raise ValidationError(
                            "この出勤依頼の店舗・枠は利用できません。"
                        )

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
                            "シフトの作成状態が変わったため承認できません。"
                        )

                    recalculate_shortages(
                        [shortage.store],
                        shortage.target_date,
                    )

                    shortage.refresh_from_db()

                    if (
                        shortage.assigned_count
                        >= shortage.required_count
                    ):
                        raise ValidationError(
                            "この枠の不足は解消しています。"
                        )

                    _, reason = eligibility(
                        staff, shortage
                    )

                    if reason:
                        raise ValidationError(
                            f"現在は承認できません：{reason}"
                        )

                row.status = (
                    2 if action == "accept" else 3
                )
                row.responded_at = timezone.now()
                row.response_note = request.POST.get("note", "").strip()[:2000]

                row.save(
                    update_fields=[
                        "status",
                        "responded_at",
                        "response_note",
                    ]
                )

                messages.success(
                    request,
                    "回答を保存しました。承認後の配置は管理者が行います。",
                )

            else:
                raise ValidationError(
                    "操作が不正です。"
                )

    except ValidationError as exc:
        messages.error(
            request,
            " ".join(exc.messages),
        )

    if request.POST.get("action") == "hold":
        if staff.role == 2:
            return redirect("shift_management:manager_main")
        return redirect("shift_management:staff_main")

    from .messaging import contact_staff

    if (
        row.requested_by_id
        and contact_staff(staff).filter(
            pk=row.requested_by_id
        ).exists()
    ):
        return redirect(
            "shift_management:conversation",
            staff_id=row.requested_by_id,
        )

    return redirect("shift_management:message")