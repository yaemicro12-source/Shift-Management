from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import (
    StaffProfile,
    StaffStoreAssignment,
    ManagementScope,
    MessageThread,
    Message,
    ShiftAdjustmentRequest,
)


def contact_staff(staff):
    """現在の所属・管理範囲から会話できる相手を取得する。"""
    today = timezone.localdate()

    memberships = StaffStoreAssignment.objects.filter(
        starts_on__lte=today,
    ).filter(
        Q(ends_on__isnull=True)
        | Q(ends_on__gte=today)
    )

    scopes = ManagementScope.objects.all()

    if staff.role == 2:
        own_scopes = scopes.filter(manager=staff)

        areas = set(
            own_scopes.exclude(
                area_id=None
            ).values_list("area_id", flat=True)
        )
        areas.update(
            own_scopes.exclude(
                store_id=None
            ).values_list("store__area_id", flat=True)
        )

        staff_ids = memberships.filter(
            store__area_id__in=areas
        ).values("staff_id")

        allowed = (
            Q(role=2)
            | Q(role=1, pk__in=staff_ids)
        )

    elif staff.role == 1:
        areas = memberships.filter(
            staff=staff
        ).values("store__area_id")

        manager_ids = scopes.filter(
            Q(area_id__in=areas)
            | Q(store__area_id__in=areas)
        ).values("manager_id")

        allowed = Q(role=2, pk__in=manager_ids)

        if staff.is_position_holder:
            allowed |= Q(
                role=1,
                is_position_holder=True,
            )

    else:
        return StaffProfile.objects.none()

    return (
        StaffProfile.objects.filter(
            allowed,
            is_active=True,
            user__is_active=True,
        )
        .exclude(pk=staff.pk)
        .select_related("user")
        .order_by("name", "firstname", "pk")
    )


def pair_threads(staff, peer):
    return MessageThread.objects.filter(
        Q(created_by=staff, recipient=peer)
        | Q(created_by=peer, recipient=staff)
    )


def chat_badge(request):
    if not request.user.is_authenticated:
        return {}

    staff = StaffProfile.objects.filter(
        user=request.user,
        is_active=True,
    ).first()

    if staff is None:
        return {}

    peers = contact_staff(staff).values("pk")

    threads = MessageThread.objects.filter(
        Q(created_by=staff, recipient_id__in=peers)
        | Q(recipient=staff, created_by_id__in=peers)
    )

    count = Message.objects.filter(
        thread__in=threads,
        sender_id__in=peers,
        read_at__isnull=True,
    ).count()

    return {"unread_message_count": count}


@login_required(login_url="shift_management:staff_login")
@require_http_methods(["GET", "POST"])
def message_page(request, staff_id=None):
    staff = get_object_or_404(
        StaffProfile,
        user=request.user,
        is_active=True,
    )

    peers = list(contact_staff(staff))
    peers_by_id = {person.pk: person for person in peers}

    peer = (
        get_object_or_404(contact_staff(staff), pk=staff_id)
        if staff_id else None
    )

    draft = ""
    error = ""

    if request.method == "POST":
        draft = request.POST.get("body", "").strip()

        if peer is None:
            error = "送信相手を選んでください。"

        elif not draft or len(draft) > 2000:
            error = "メッセージは1〜2000文字で入力してください。"

        else:
            with transaction.atomic():
                # 同じ相手とのスレッドの重複作成を防ぐ
                list(
                    StaffProfile.objects.select_for_update()
                    .filter(pk__in=[staff.pk, peer.pk])
                    .order_by("pk")
                )

                peer = get_object_or_404(
                    contact_staff(staff),
                    pk=peer.pk,
                )

                thread = (
                    pair_threads(staff, peer)
                    .order_by("pk")
                    .first()
                )

                if thread is None:
                    thread = MessageThread.objects.create(
                        created_by=staff,
                        recipient=peer,
                    )

                Message.objects.create(
                    thread=thread,
                    sender=staff,
                    body=draft,
                )

            return redirect(
                "shift_management:conversation",
                staff_id=peer.pk,
            )

    now = timezone.now()

    if peer:
        Message.objects.filter(
            thread__in=pair_threads(staff, peer),
            sender=peer,
            read_at__isnull=True,
            sent_at__lte=now,
        ).update(read_at=now)

    threads = MessageThread.objects.filter(
        Q(created_by=staff, recipient_id__in=peers_by_id)
        | Q(recipient=staff, created_by_id__in=peers_by_id)
    )

    thread_peers = {
        row.pk: (
            row.recipient_id
            if row.created_by_id == staff.pk
            else row.created_by_id
        )
        for row in threads
    }

    contacts = {
        person.pk: {
            "staff": person,
            "preview": "メッセージを送れます",
            "latest": None,
            "unread": 0,
            "pending": 0,
        }
        for person in peers
    }

    timeline = []

    def preview(peer_id, body, at):
        contact = contacts[peer_id]

        if contact["latest"] is None or at >= contact["latest"]:
            contact.update(preview=body, latest=at)

    normal_messages = Message.objects.filter(
        thread_id__in=thread_peers,
        sender_id__in=[staff.pk, *peers_by_id],
    ).order_by("sent_at", "pk")

    for row in normal_messages:
        peer_id = thread_peers[row.thread_id]

        if row.sender_id not in (staff.pk, peer_id):
            continue

        preview(peer_id, row.body, row.sent_at)

        if row.sender_id != staff.pk and row.read_at is None:
            contacts[peer_id]["unread"] += 1

        if peer and peer_id == peer.pk:
            timeline.append({
                "kind": "text",
                "at": row.sent_at,
                "mine": row.sender_id == staff.pk,
                "body": row.body,
                "read": row.read_at is not None,
            })

    requests = ShiftAdjustmentRequest.objects.filter(
        Q(requested_by=staff, recipient_id__in=peers_by_id)
        | Q(recipient=staff, requested_by_id__in=peers_by_id)
    ).select_related(
        "shortage__store",
        "shortage__shift_time_slot",
    )

    for row in requests:
        mine = row.requested_by_id == staff.pk
        peer_id = (
            row.recipient_id if mine else row.requested_by_id
        )

        can_reply = (
            not mine
            and row.status == 1
            and row.response_deadline > now
        )

        if row.status == 1:
            status = (
                "未回答"
                if row.response_deadline > now
                else "期限切れ"
            )
        else:
            status = row.get_status_display()

        preview(
            peer_id,
            f"出勤依頼 {row.shortage.target_date:%m/%d}・{status}",
            row.requested_at,
        )

        if can_reply:
            contacts[peer_id]["pending"] += 1

        if peer and peer_id == peer.pk:
            timeline.append({
                "kind": "request",
                "at": row.requested_at,
                "mine": mine,
                "body": row.message,
                "row": row,
                "status": status,
                "can_reply": can_reply,
            })

        if row.responded_at:
            responder = "相手" if mine else "あなた"
            body = (
                f"{responder}が出勤依頼"
                f"（{row.shortage.target_date:%m/%d}）"
                f"に「{status}」と回答しました。"
            )

            if row.response_note:
                body += "\n" + row.response_note

            preview(peer_id, body, row.responded_at)

            if peer and peer_id == peer.pk:
                timeline.append({
                    "kind": "reply",
                    "at": row.responded_at,
                    "mine": not mine,
                    "body": body,
                })

    contacts = list(contacts.values())
    contacts.sort(
        key=lambda row: (
            row["latest"].timestamp()
            if row["latest"]
            else float("-inf")
        ),
        reverse=True,
    )

    timeline.sort(key=lambda row: row["at"])

    return render(
        request,
        "shift_management/message_chat.html",
        {
            "contacts": contacts,
            "peer": peer,
            "timeline": timeline,
            "draft": draft,
            "error": error,
        },
    )