import calendar
from datetime import date, timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .decorators import manager_required
from .messaging import contact_staff
from .models import (
    Area,
    StaffProfile,
    StaffStoreAssignment,
    ShiftSubmission,
    ShiftAssignment,
    ShiftSchedule,
    StaffEvaluation,
    EvaluationSetting,
    Message,
    MessageThread,
    ShiftAdjustmentRequest,
    Notification,
    ShiftNotificationPlan,
)
from .shift_controls import allowed_stores


def plan_months():
    today = timezone.localdate()
    base = today.year * 12 + today.month - 1
    return [
        {
            "year": (base + i) // 12,
            "month": (base + i) % 12 + 1,
            "value": f"{(base+i)//12:04d}-{(base+i)%12+1:02d}",
        }
        for i in range(3)
    ]


def sync_notifications(request):
    if getattr(request, "_notifications_synced", False):
        return
    request._notifications_synced = True
    if not request.user.is_authenticated:
        return
    staff = StaffProfile.objects.filter(user=request.user, is_active=True).first()
    if staff is None:
        return
    today, now = timezone.localdate(), timezone.now()
    active = set()

    def put(key, category, title, body, url):
        active.add(key)
        row, created = Notification.objects.get_or_create(
            recipient=staff,
            event_key=key,
            defaults={"category": category, "title": title, "body": body, "url": url},
        )
        if not created:
            changes = []
            for field, value in [("title", title), ("body", body), ("url", url)]:
                if getattr(row, field) != value:
                    setattr(row, field, value)
                    changes.append(field)
            if row.resolved_at is not None:
                row.resolved_at = None
                row.read_at = None
                changes += ["resolved_at", "read_at"]
            if changes:
                row.save(update_fields=changes)

    # 通常メッセージと出勤依頼。現在会話できる相手に限定する。
    peers = contact_staff(staff).values("pk")
    threads = MessageThread.objects.filter(
        Q(created_by=staff, recipient_id__in=peers)
        | Q(recipient=staff, created_by_id__in=peers)
    )
    for row in Message.objects.filter(
        thread__in=threads,
        sender_id__in=peers,
        read_at__isnull=True,
    ).select_related("sender", "thread"):
        other_id = (
            row.thread.recipient_id
            if row.thread.created_by_id == staff.pk
            else row.thread.created_by_id
        )
        if row.sender_id != other_id:
            continue
        put(
            f"message:{row.pk}",
            "message",
            f"{row.sender.name}さんからメッセージ",
            row.body[:100],
            reverse("shift_management:conversation", args=[row.sender_id]),
        )

    for row in ShiftAdjustmentRequest.objects.filter(
        recipient=staff,
        requested_by_id__in=peers,
        status=1,
        response_deadline__gt=now,
    ).select_related("requested_by", "shortage__store"):
        put(
            f"request:{row.pk}",
            "request",
            "出勤依頼が届いています",
            f'{row.shortage.target_date:%m/%d} {row.shortage.store.name if row.shortage.store else ""}／{row.requested_by.name}さんから',
            reverse("shift_management:conversation", args=[row.requested_by_id]),
        )

    months = plan_months()
    first = date(months[0]["year"], months[0]["month"], 1)
    final = months[-1]
    last = date(
        final["year"],
        final["month"],
        calendar.monthrange(final["year"], final["month"])[1],
    )
    memberships = StaffStoreAssignment.objects.filter(
        staff=staff, starts_on__lte=last
    ).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=first))
    area_ids = set(memberships.values_list("store__area_id", flat=True))
    area_ids.update(
        ShiftAssignment.objects.filter(
            staff=staff, work_date__range=(first, last), status=2
        ).values_list("store__area_id", flat=True)
    )
    managed_areas = (
        set(allowed_stores(staff).values_list("area_id", flat=True))
        if staff.role == 2
        else set()
    )
    area_ids.update(managed_areas)
    month_filter = Q(pk__in=[])
    for value in months:
        month_filter |= Q(year=value["year"], month=value["month"])

    for schedule in ShiftSchedule.objects.filter(
        month_filter, area_id__in=area_ids, status=4
    ).select_related("area"):
        month_first = date(schedule.year, schedule.month, 1)
        month_last = date(
            schedule.year,
            schedule.month,
            calendar.monthrange(schedule.year, schedule.month)[1],
        )
        relevant = (
            schedule.area_id in managed_areas
            or memberships.filter(
                store__area_id=schedule.area_id, starts_on__lte=month_last
            )
            .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=month_first))
            .exists()
            or ShiftAssignment.objects.filter(
                staff=staff,
                store__area_id=schedule.area_id,
                work_date__range=(month_first, month_last),
                status=2,
            ).exists()
        )
        if relevant:
            stamp = (
                schedule.published_at.isoformat()
                if schedule.published_at
                else "published"
            )
            put(
                f"published:{schedule.pk}:{stamp}",
                "published",
                f"{schedule.year}年{schedule.month}月のシフトが公開されました",
                schedule.area.name,
                reverse(
                    "shift_management:day_shift_detail",
                    args=[schedule.year, schedule.month, 1],
                ),
            )

    # 前月分の評価。評価画面と同じ対象者判定を使用する。
    if staff.role == 2 or staff.is_position_holder:
        from .views import get_evaluation_areas, get_evaluation_targets

        previous = today.replace(day=1) - timedelta(days=1)
        for area in get_evaluation_areas(staff, previous.year, previous.month):
            setting = EvaluationSetting.objects.filter(
                area=area, is_enabled=True
            ).first()
            if setting is None:
                continue
            deadline = today.replace(
                day=min(
                    setting.deadline_day,
                    calendar.monthrange(today.year, today.month)[1],
                )
            )
            if not deadline - timedelta(days=3) <= today <= deadline:
                continue
            targets = list(
                get_evaluation_targets(staff, previous.year, previous.month, area)
            )
            submitted = set(
                StaffEvaluation.objects.filter(
                    area=area,
                    evaluator=staff,
                    year=previous.year,
                    month=previous.month,
                    submitted_at__isnull=False,
                ).values_list("staff_id", flat=True)
            )
            missing = [person for person in targets if person.pk not in submitted]
            if missing:
                phase = "today" if today == deadline else "three-days"
                title = (
                    "スタッフ評価の入力期限は本日です"
                    if today == deadline
                    else "スタッフ評価の入力期限が近づいています"
                )
                put(
                    f"evaluation:{area.pk}:{previous.year}-{previous.month}:{deadline}:{phase}",
                    "evaluation",
                    title,
                    f"{area.name}／{previous.month}月分：未入力{len(missing)}人。期限 {deadline:%m/%d}",
                    reverse("shift_management:evaluation_list"),
                )

    if staff.role == 2:
        for plan in ShiftNotificationPlan.objects.filter(
            month_filter, area_id__in=managed_areas
        ).select_related("area"):
            month_first = date(plan.year, plan.month, 1)
            month_last = date(
                plan.year, plan.month, calendar.monthrange(plan.year, plan.month)[1]
            )
            url = (
                reverse("shift_management:shift_management")
                + f"?area={plan.area_id}&year={plan.year}&month={plan.month}"
            )
            label = f"{plan.area.name}／{plan.year}年{plan.month}月"
            if today >= plan.submission_deadline:
                eligible_memberships = StaffStoreAssignment.objects.filter(
                    store__area=plan.area, starts_on__lte=month_last
                ).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=month_first))
                expected = StaffProfile.objects.filter(
                    is_active=True,
                    role__in=[1, 2],
                    joined_on__lte=month_last,
                    pk__in=eligible_memberships.values("staff_id"),
                )
                submitted = ShiftSubmission.objects.filter(
                    year=plan.year, month=plan.month, is_latest=True, status__in=[2, 3]
                ).values("staff_id")
                missing = list(expected.exclude(pk__in=submitted))
                if missing:
                    names = "、".join(person.name for person in missing[:6])
                    put(
                        f"submission:{plan.pk}:{plan.submission_deadline}",
                        "submission",
                        "希望シフトの未提出者がいます",
                        f"{label}：{len(missing)}人未提出（{names}）。提出期限 {plan.submission_deadline:%m/%d}",
                        url,
                    )
            schedules = ShiftSchedule.objects.filter(
                area=plan.area, year=plan.year, month=plan.month
            )
            if (
                today >= plan.creation_date
                and not schedules.filter(status__gte=2).exists()
            ):
                put(
                    f"creation:{plan.pk}:{plan.creation_date}",
                    "creation",
                    "シフトの作成予定日になっています",
                    f"{label}／作成予定日 {plan.creation_date:%m/%d}。自動生成が未完了です。",
                    url,
                )
            if (
                today >= plan.publication_date
                and not schedules.filter(status=4).exists()
            ):
                put(
                    f"announcement:{plan.pk}:{plan.publication_date}",
                    "announcement",
                    "シフトの発表予定日になっています",
                    f"{label}／発表予定日 {plan.publication_date:%m/%d}。スタッフへの公開が未完了です。",
                    url,
                )

    # 回答・提出・公開などで解消した通知を未読件数から除外する。
    Notification.objects.filter(recipient=staff, resolved_at__isnull=True).exclude(
        event_key__in=active
    ).update(resolved_at=now)


def notification_context(request):
    sync_notifications(request)
    if not request.user.is_authenticated:
        return {}
    return {
        "notification_count": Notification.objects.filter(
            recipient__user=request.user, read_at__isnull=True, resolved_at__isnull=True
        ).count()
    }


@login_required(login_url="shift_management:staff_login")
@require_GET
def notification_list(request):
    sync_notifications(request)
    rows = Notification.objects.filter(recipient__user=request.user)
    return render(
        request, "shift_management/notifications.html", {"notifications": rows[:100]}
    )


@login_required(login_url="shift_management:staff_login")
@require_POST
def notification_open(request, notification_id):
    row = get_object_or_404(
        Notification, pk=notification_id, recipient__user=request.user
    )
    if row.read_at is None:
        row.read_at = timezone.now()
        row.save(update_fields=["read_at"])
    if row.url.startswith("/") and url_has_allowed_host_and_scheme(
        row.url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(row.url)
    return redirect("shift_management:notifications")


@login_required(login_url="shift_management:staff_login")
@require_POST
def notification_read_all(request):
    Notification.objects.filter(
        recipient__user=request.user, read_at__isnull=True
    ).update(read_at=timezone.now())
    return redirect("shift_management:notifications")


class NotificationPlanForm(forms.Form):
    area = forms.ModelChoiceField(
        queryset=Area.objects.none(), widget=forms.HiddenInput
    )
    period = forms.ChoiceField(widget=forms.HiddenInput)
    submission_deadline = forms.DateField(
        label="希望シフト提出期限", widget=forms.DateInput(attrs={"type": "date"})
    )
    creation_date = forms.DateField(
        label="シフト作成予定日", widget=forms.DateInput(attrs={"type": "date"})
    )
    publication_date = forms.DateField(
        label="シフト発表予定日", widget=forms.DateInput(attrs={"type": "date"})
    )

    def __init__(self, *args, areas, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["area"].queryset = areas
        self.fields["period"].choices = [
            (row["value"], row["value"]) for row in plan_months()
        ]

    def clean(self):
        data = super().clean()
        dates = [
            data.get(field)
            for field in ["submission_deadline", "creation_date", "publication_date"]
        ]
        if all(dates) and not dates[0] <= dates[1] <= dates[2]:
            raise forms.ValidationError(
                "提出期限 → 作成予定日 → 発表予定日の順に設定してください。"
            )
        return data


@manager_required
def notification_settings(request):
    manager = request.user.staffprofile
    stores = allowed_stores(manager)
    ids = set(stores.values_list("pk", flat=True))
    areas = Area.objects.filter(store__in=stores).distinct().order_by("name")
    allowed_ids = [
        area.pk
        for area in areas
        if set(area.store_set.values_list("pk", flat=True)).issubset(ids)
    ]
    areas = areas.filter(pk__in=allowed_ids)
    months = plan_months()
    if not areas.exists():
        return render(
            request,
            "shift_management/notification_settings.html",
            {"areas": areas, "month_options": months},
        )
    params = request.POST if request.method == "POST" else request.GET
    try:
        area_id = int(params.get("area") or areas.first().pk)
    except (TypeError, ValueError):
        messages.error(request, "エリアを正しく選択してください。")
        return redirect("shift_management:notification_settings")
    selected_area = get_object_or_404(areas, pk=area_id)
    period = params.get("period", months[1]["value"])
    if period not in {row["value"] for row in months}:
        messages.error(request, "対象月は今月・来月・再来月から選んでください。")
        return redirect("shift_management:notification_settings")
    year, month = map(int, period.split("-"))
    plan = ShiftNotificationPlan.objects.filter(
        area=selected_area, year=year, month=month
    ).first()
    initial = {"area": selected_area.pk, "period": period}
    if plan:
        initial.update(
            {
                field: getattr(plan, field).isoformat()
                for field in [
                    "submission_deadline",
                    "creation_date",
                    "publication_date",
                ]
            }
        )
    form = NotificationPlanForm(
        request.POST if request.method == "POST" else None, areas=areas, initial=initial
    )
    if request.method == "POST" and form.is_valid():
        ShiftNotificationPlan.objects.update_or_create(
            area=selected_area,
            year=year,
            month=month,
            defaults={
                field: form.cleaned_data[field]
                for field in [
                    "submission_deadline",
                    "creation_date",
                    "publication_date",
                ]
            },
        )
        messages.success(request, "通知日程を保存しました。")
        return redirect(
            reverse("shift_management:notification_settings")
            + f"?area={selected_area.pk}&period={period}"
        )
    return render(
        request,
        "shift_management/notification_settings.html",
        {
            "areas": areas,
            "month_options": months,
            "selected_area": selected_area,
            "period": period,
            "year": year,
            "month": month,
            "form": form,
        },
    )
