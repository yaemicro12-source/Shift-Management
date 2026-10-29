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
    from .request_actions import save_response
    from .messaging import contact_staff

    staff = get_object_or_404(
        StaffProfile,
        user=request.user,
        is_active=True,
    )

    row = get_object_or_404(
        ShiftAdjustmentRequest,
        pk=request_id,
        recipient=staff,
    )
    action = request.POST.get("action")

    try:
        if action == "hold":
            if (
                row.status != 1
                or row.response_deadline <= timezone.now()
            ):
                raise ValidationError(
                    "回答済み・受付終了、または期限切れです。"
                )

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

        else:
            save_response(
                staff,
                request_id,
                action,
                request.POST.get("note", ""),
            )
            messages.success(
                request,
                "回答を保存しました。承認した場合は管理者の反映待ちです。",
            )

    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))

    if action == "hold":
        home = (
            "shift_management:manager_main"
            if staff.role == 2
            else "shift_management:staff_main"
        )
        return redirect(home)

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