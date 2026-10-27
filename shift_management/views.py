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

@login_required(login_url="shift_management:staff_login")
def shift_submission(request):
    """シフト提出"""
    availability_list = []
    today = date.today()

    if today.month == 12:
        year = today.year + 1
        month = 1
    else:
        year = today.year
        month = today.month + 1
    day = today.day
    last_day = calendar.monthrange(year,month)[1] 
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    days = []
    staff = StaffProfile.objects.get(user=request.user)
    submission_type = staff.get_submission_type_display()
    

    for day in range(1, last_day + 1):
        target_date = date(year, month, day)
        weekday_name = weekdays[target_date.weekday()]

        days.append({
            "day": day,
            "weekday": weekday_name,
        })

    context = {
        "day": day,
        "month":month, 
        "days":days,
        "year":year,
        "submission_type":submission_type
    }
       
    if request.method == "POST":
        for day in range(1,last_day+1):
            if f"day_{day}" in request.POST:
                availability_list.append(True)
            else:
                availability_list.append(False)

        if staff.submission_type == 2:
            availability_list= [not x for x in availability_list]

        with transaction.atomic():
            ShiftSubmission.objects.filter(staff=staff,year=year,month=month).update(is_latest=False)
            submission_record = ShiftSubmission.objects.create(
            staff = staff,
            year = year,
            month = month,     
            submission_type = staff.submission_type,
            status = 2,
            submitted_at = timezone.now()
            )

            for day, is_available in enumerate(availability_list, start=1):
                ShiftPreference.objects.create(
                    target_date=date(year, month, day),
                    is_available=is_available,
                    submission=submission_record,
                    available_start_time=request.POST.get(f"start_{day}") or None,
                    available_end_time=request.POST.get(f"end_{day}") or None,
                    note=request.POST.get(f"note_{day}")
                )
        

    return render(
        request,
        'shift_management/shift_submission.html',
        context
    )


@manager_required
def manager_main(request):
    context = get_main_shift_context(request)

    return render(
        request,
        "shift_management/manager_main.html",
        context,
    )

@manager_required
def edit_shift(request):
    """シフト編集画面"""
    today = date.today()
    now_month = today.month
    now_year = today.year
    stores = Store.objects.all()
    timeslots = ShiftTimeSlot.objects.all()
        # 自分が管理するエリアの店舗だけ登録可能にする
    manager = request.user.staffprofile
    scopes = ManagementScope.objects.filter(manager=manager)

    managed_area_ids = set(
        scopes.exclude(area__isnull=True)
        .values_list("area_id", flat=True)
    )

    managed_area_ids.update(
        Store.objects.filter(
            id__in=scopes.exclude(store__isnull=True)
            .values_list("store_id", flat=True)
        ).values_list("area_id", flat=True)
    )

    editable_stores = Store.objects.filter(
        area_id__in=managed_area_ids
    )
    shifts = ShiftAssignment.objects.filter(
    staff__user=request.user)
    selects = {"エリア","店舗","スタッフ"}




    date_s = request.GET.get("date")
    if date_s == None:
        previous_month = request.session.get("month")
        previous_year = request.session.get("year")
        display_day = 1

        if previous_month == None:
            display_year = now_year
            if now_month <= 11:
                display_month = now_month + 1

            else :
                display_month = 1
                display_year = display_year + 1
        else :
            display_month = previous_month
            display_year = previous_year
    else :
        select_date = date.fromisoformat(date_s)
        display_year = select_date.year
        display_month = select_date.month
        display_day = select_date.day

    request.session["month"] = display_month
    request.session["year"] = display_year
    context = get_calendar_data(display_year, display_month, display_day)
    shift_id = request.GET.get("shift_id")
    selected_shift = None

    if shift_id:
        selected_shift = get_object_or_404(
            ShiftAssignment,
            id=shift_id
        )

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "delete" and selected_shift is not None:
            deleted_date = selected_shift.work_date
            selected_shift.delete()

            return redirect(
                f"/edit_shift/?date={deleted_date:%Y-%m-%d}"
            )
        form = ShiftAssignmentForm(
            request.POST,
            instance=selected_shift
        )
        form.fields["store"].queryset = editable_stores

        if action == "save" and form.is_valid():
            shift = form.save(commit=False)
            shift.status = 1
            shift.save()

            return redirect(
                f"/edit_shift/?date={shift.work_date:%Y-%m-%d}"
            )
    else:
        form = ShiftAssignmentForm(
            instance=selected_shift,
            initial={
                "work_date": f"{display_year}-{display_month:02d}-{display_day:02d}"
            }
        )
    form.fields["store"].queryset = editable_stores
    hours = range(10, 23)
    staffs = StaffProfile.objects.all()
    context["staffs"] = staffs
    context["staff_departure_times"] = {
        str(staff.id): staff.last_train_departure_time.strftime("%H:%M")
        for staff in staffs
        if staff.last_train_departure_time is not None
    }
    context["staff_name"] = request.user.username
    context["shifts"] = shifts
    context["selects"] = selects
    context["hours"] = hours
    context["year"] = display_year
    context["month"] = display_month
    context["display_day"] = display_day
    context["stores"] = stores
    context["timeslots"] = timeslots

    context["form"] = form

    
    # グラフで表示する日付
    selected_date = date(
        display_year,
        display_month,
        display_day
    )

    # その日の全スタッフのシフト
    # 画面で選択されている店舗
    selected_store_id = request.GET.get("store", "")

    # 選択日のシフトを取得
    day_shifts = ShiftAssignment.objects.filter(
        work_date=selected_date
    )

    # 店舗が選択されている場合だけ絞り込む
    if selected_store_id:
        day_shifts = day_shifts.filter(
            store_id=selected_store_id
        )

    day_shifts = day_shifts.select_related(
        "staff"
    ).order_by("id")

    PIXELS_PER_HOUR = 24

    
    # 選択中の店舗を取得
    if selected_store_id:
        selected_store = stores.filter(
            id=selected_store_id
        ).first()
    else:
        selected_store = None

    # 店舗ごとの営業時間を分単位に変換
    store_hours = []

    for store in stores:
        opening_minutes = (
            store.opening_time.hour * 60
            + store.opening_time.minute
        )

        closing_minutes = (
            store.closing_time.hour * 60
            + store.closing_time.minute
        )

        # 閉店が開店以前なら24時以降として扱う
        if closing_minutes <= opening_minutes:
            closing_minutes += 24 * 60

        store_hours.append({
            "id": store.id,
            "opening": opening_minutes,
            "closing": closing_minutes,
        })

    # 選択店舗があればその営業時間を使用
    if selected_store:
        hours_data = next(
            (
                item for item in store_hours
                if item["id"] == selected_store.id
            ),
            None
        )
    else:
        hours_data = None

    if hours_data:
        chart_start_minutes = hours_data["opening"]
        chart_end_minutes = hours_data["closing"]

    elif store_hours:
        # すべての店舗の営業時間を含める
        chart_start_minutes = min(
            item["opening"] for item in store_hours
        )
        chart_end_minutes = max(
            item["closing"] for item in store_hours
        )

    else:
        chart_start_minutes = 6 * 60
        chart_end_minutes = 24 * 60

    # 開店1時間前・閉店1時間後
    CHART_START_HOUR = (chart_start_minutes - 60) // 60
    CHART_END_HOUR = (chart_end_minutes + 60 + 59) // 60

    chart_height = (
        CHART_END_HOUR - CHART_START_HOUR
    ) * PIXELS_PER_HOUR

    chart_hours = [
        {
            "label": f"{hour:02d}:00",
            "top": (hour - CHART_START_HOUR) * PIXELS_PER_HOUR,
        }
        for hour in range(
            CHART_START_HOUR,
            CHART_END_HOUR + 1
        )
    ]
    existing_shift_pairs = [
        {
            "staff_id": shift["staff_id"],
            "date": shift["work_date"].isoformat(),
        }
        for shift in ShiftAssignment.objects.values(
            "staff_id", "work_date"
        ).distinct()
    ]

    chart_staffs = []

    for staff in staffs:
        staff_shifts = [
            shift for shift in day_shifts
            if shift.staff_id == staff.id
        ]

        bars = []

        for shift in staff_shifts:
            if not shift.start_time or not shift.end_time:
                continue

            start_minutes = (
                shift.start_time.hour * 60
                + shift.start_time.minute
            )

            end_minutes = (
                shift.end_time.hour * 60
                + shift.end_time.minute
            )

            # 日付をまたぐ勤務を24時以降として扱う
            if end_minutes <= start_minutes:
                end_minutes += 24 * 60

            # 深夜0時以降の勤務を24時以降に配置
            if (
                CHART_END_HOUR > 24
                and start_minutes < CHART_START_HOUR * 60
            ):
                start_minutes += 24 * 60
                end_minutes += 24 * 60

            # 勤務バーの位置と高さ
            top = (
                start_minutes - CHART_START_HOUR * 60
            ) * PIXELS_PER_HOUR / 60

            height = (
                end_minutes - start_minutes
            ) * PIXELS_PER_HOUR / 60

            visible_top = max(0, top)
            visible_bottom = min(chart_height, top + height)

            if visible_bottom <= visible_top:
                continue

            bars.append({
                "id": shift.id,
                "date": shift.work_date,
                "start": shift.start_time.strftime("%H:%M"),
                "end": shift.end_time.strftime("%H:%M"),
                "top": round(visible_top, 2),
                "height": round(
                    visible_bottom - visible_top, 2
                ),
            })

        # スタッフ単位の開始時間・登録順
        first_start = min(
            (
                shift.start_time.hour * 60
                + shift.start_time.minute
                for shift in staff_shifts
                if shift.start_time
            ),
            default=9999,
        )

        first_order = min(
            (shift.id for shift in staff_shifts),
            default=999999,
        )

        chart_staffs.append({
            "staff": staff,
            "color": staff.color or "#b9d7f5",
            "bars": bars,
            "sort_start": first_start,
            "sort_order": first_order,
        })

    # 初期表示は開始時間順
    chart_staffs.sort(
        key=lambda item: (
            item["sort_start"],
            item["sort_order"],
        )
    )

    # 前月・翌月の移動先
    if display_month == 1:
        prev_month_date = date(display_year - 1, 12, 1)
    else:
        prev_month_date = date(display_year, display_month - 1, 1)

    if display_month == 12:
        next_month_date = date(display_year + 1, 1, 1)
    else:
        next_month_date = date(display_year, display_month + 1, 1)

    
    context.update({
        "chart_hours": chart_hours,
        "chart_staffs": chart_staffs,
        "display_day": display_day,
        "selected_shift": selected_shift,
        "prev_month_date": prev_month_date,
        "next_month_date": next_month_date,
        "chart_height": chart_height,
        "selected_store_id": selected_store_id,
        "existing_shift_pairs": existing_shift_pairs,
        
    })

    return render(
        request,'shift_management/edit_shift.html',context
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
def generate_shift(request):
    today = timezone.localdate()
    month_start = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    year = month_start.year
    month = month_start.month
    month_end = date(year, month, calendar.monthrange(year, month)[1])

    try:
        with transaction.atomic():
            # 同じ管理者による生成処理を直列化
            manager = StaffProfile.objects.select_for_update().get(
                user=request.user
            )

            scopes = ManagementScope.objects.filter(manager=manager)
            stores = list(
                Store.objects.select_for_update().filter(
                    Q(area_id__in=scopes.values("area_id"))
                    | Q(id__in=scopes.values("store_id"))
                ).distinct().order_by("id")
            )

            if not stores:
                raise ValueError("担当店舗が設定されていません。")

            store_ids = [store.id for store in stores]

            # 手動登録・確定分を含め、既存データは上書きしない
            if ShiftAssignment.objects.filter(
                store_id__in=store_ids,
                work_date__range=(month_start, month_end),
                status__in=[1, 2],
            ).exists():
                raise ValueError(
                    "対象月にシフトが登録済みです。"
                    "重複を防ぐため、自動生成を停止しました。"
                )

            if ShiftShortage.objects.filter(
                store_id__in=store_ids,
                target_date__range=(month_start, month_end),
            ).exists():
                raise ValueError(
                    "対象月に不足記録が登録済みです。"
                    "重複を防ぐため、自動生成を停止しました。"
                )

            slots = list(
                ShiftTimeSlot.objects.filter(
                    store_id__in=store_ids,
                    is_active=True,
                ).select_related("store").order_by("id")
            )
            if not slots:
                raise ValueError("担当店舗に有効なシフト枠がありません。")

            for slot in slots:
                if (
                    _shift_minutes(slot.end_time, end=True)
                    <= _shift_minutes(slot.start_time)
                ):
                    raise ValueError(
                        f"{slot.store.name}・{slot.name}："
                        "24:00を超える深夜勤務は今回の対象外です。"
                    )

            rules = list(
                RequiredStaffingRule.objects.filter(
                    store_id__in=store_ids,
                    shift_time_slot_id__in=[slot.id for slot in slots],
                )
            )
            holidays = JPHoliday()
            default_counts = {"朝番": 1, "昼番": 2, "夜番": 2}

            def required_count(day, slot):
                # 特定日の設定を最優先
                exact = [
                    rule for rule in rules
                    if rule.store_id == slot.store_id
                    and rule.shift_time_slot_id == slot.id
                    and rule.target_date == day
                ]
                if len(exact) > 1:
                    raise ValueError(
                        f"{day}・{slot.store.name}・{slot.name}："
                        "特定日の必要人数設定が重複しています。"
                    )
                if exact:
                    return exact[0].required_count

                # 今回指定した通常人数：平日1/2/2、土日祝2/3/3
                if slot.name not in default_counts:
                    raise ValueError(
                        f"{slot.store.name}・{slot.name}："
                        "必要人数を特定日設定に登録してください。"
                    )
                holiday = day.weekday() >= 5 or holidays.is_holiday(day)
                return default_counts[slot.name] + int(holiday)

            preferences = list(
                ShiftPreference.objects.filter(
                    submission__is_latest=True,
                    submission__year=year,
                    submission__month=month,
                    submission__status__in=[2, 3],
                    submission__staff__is_active=True,
                    submission__staff__is_trainee=False,
                    target_date__range=(month_start, month_end),
                    is_available=True,
                ).select_related("submission__staff")
            )

            preferences_by_day = defaultdict(list)
            seen = set()
            for preference in preferences:
                key = (
                    preference.submission.staff_id,
                    preference.target_date,
                )
                if key in seen:
                    raise ValueError(
                        "同じスタッフ・日付の最新希望が重複しています。"
                    )
                seen.add(key)
                preferences_by_day[preference.target_date].append(preference)

            memberships = list(
                StaffStoreAssignment.objects.filter(
                    store_id__in=store_ids,
                    starts_on__lte=month_end,
                ).filter(
                    Q(ends_on__isnull=True)
                    | Q(ends_on__gte=month_start)
                )
            )

            # 月初の週全体と月末後の連勤も確認
            history_start = month_start - timedelta(
                days=max(5, month_start.weekday())
            )
            history_end = month_end + timedelta(days=5)

            history = ShiftAssignment.objects.filter(
                work_date__range=(history_start, history_end),
                status__in=[1, 2],
            ).select_related("shift_time_slot")

            working_days = defaultdict(set)
            weekly_minutes = defaultdict(int)
            monthly_minutes = defaultdict(int)

            for shift in history:
                if shift.staff_id is None:
                    continue

                working_days[shift.staff_id].add(shift.work_date)

                start = shift.start_time
                end = shift.end_time
                if shift.shift_time_slot:
                    if start is None:
                        start = shift.shift_time_slot.start_time
                    if end is None:
                        end = shift.shift_time_slot.end_time
                if start is None or end is None:
                    raise ValueError(
                        f"既存シフトID {shift.id} の勤務時刻が未設定です。"
                    )

                duration = (
                    _shift_minutes(end, end=True) - _shift_minutes(start)
                )
                if duration <= 0:
                    raise ValueError(
                        f"既存シフトID {shift.id} の時刻を確認してください。"
                    )

                duration = max(0, duration - shift.break_minutes)
                week = shift.work_date - timedelta(
                    days=shift.work_date.weekday()
                )
                weekly_minutes[shift.staff_id, week] += duration
                if month_start <= shift.work_date <= month_end:
                    monthly_minutes[shift.staff_id] += duration

            def candidates_for(day, slot):
                result = []
                start = _shift_minutes(slot.start_time)
                end = _shift_minutes(slot.end_time, end=True)
                week = day - timedelta(days=day.weekday())
                week_end = week + timedelta(days=6)

                member_ids = {
                    item.staff_id for item in memberships
                    if item.store_id == slot.store_id
                    and item.starts_on <= day
                    and (item.ends_on is None or item.ends_on >= day)
                }

                for preference in preferences_by_day[day]:
                    staff = preference.submission.staff
                    days = working_days[staff.id]

                    if staff.id not in member_ids:
                        continue
                    if staff.joined_on > day or day in days:
                        continue
                    if (
                        preference.available_start_time is None
                        or preference.available_end_time is None
                    ):
                        continue

                    actual_end = end
                    if staff.last_train_departure_time is not None:
                        actual_end = min(
                            end,
                            _shift_minutes(
                                staff.last_train_departure_time, end=True
                            ),
                        )

                    if actual_end <= start or end - actual_end > 15:
                        continue
                    if (
                        _shift_minutes(preference.available_start_time) > start
                        or _shift_minutes(
                            preference.available_end_time, end=True
                        ) < actual_end
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
                    if sum(week <= value <= week_end for value in days) >= 5:
                        continue

                    consecutive = 1
                    for direction in (-1, 1):
                        check = day + timedelta(days=direction)
                        while check in days:
                            consecutive += 1
                            check += timedelta(days=direction)
                    if consecutive > 5:
                        continue

                    result.append((staff, actual_end, duration))

                result.sort(key=lambda item: (
                    not item[0].shift_priority,
                    monthly_minutes[item[0].id],
                    item[0].id,
                ))
                return result

            generated = 0
            shortage_slots = 0
            shortage_people = 0

            for number in range(1, month_end.day + 1):
                day = date(year, month, number)
                week = day - timedelta(days=day.weekday())

                # 候補が少ない枠を先に処理。同条件なら夜番を優先
                daily_slots = sorted(
                    slots,
                    key=lambda slot: (
                        len(candidates_for(day, slot))
                        - required_count(day, slot),
                        -_shift_minutes(slot.end_time, end=True),
                        slot.id,
                    ),
                )

                for slot in daily_slots:
                    required = required_count(day, slot)
                    if required == 0:
                        continue

                    candidates = candidates_for(day, slot)
                    end = _shift_minutes(slot.end_time, end=True)

                    full = [
                        item for item in candidates if item[1] == end
                    ]
                    early = [
                        item for item in candidates if item[1] < end
                    ]

                    selected = full[:required]

                    # 終了まで残る人がいる場合だけ早退者を配置
                    if selected:
                        selected.extend(
                            early[:required - len(selected)]
                        )

                    for staff, actual_end, duration in selected:
                        ShiftAssignment.objects.create(
                            staff=staff,
                            store=slot.store,
                            work_date=day,
                            shift_time_slot=slot,
                            start_time=slot.start_time,
                            end_time=_shift_time(actual_end),
                            status=1,
                            is_training=False,
                            created_by=manager,
                        )
                        working_days[staff.id].add(day)
                        weekly_minutes[staff.id, week] += duration
                        monthly_minutes[staff.id] += duration
                        generated += 1

                    missing = required - len(selected)
                    if missing:
                        ShiftShortage.objects.create(
                            store=slot.store,
                            target_date=day,
                            shift_time_slot=slot,
                            required_count=required,
                            assigned_count=len(selected),
                            note="自動生成：希望・退勤・勤務上限などで配置不足",
                        )
                        shortage_slots += 1
                        shortage_people += missing

    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("shift_management:manager_main")

    messages.success(
        request,
        f"{year}年{month}月：下書き{generated}件を作成しました。"
        f"不足{shortage_slots}枠・延べ{shortage_people}人です。",
    )
    return redirect(
        f"/edit_shift/?date={month_start:%Y-%m-%d}"
    )    



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
        if today.day > setting.deadline_day:
            messages.error(request, "今月の評価提出期限を過ぎています。")
            return redirect(
                "shift_management:evaluation_detail",
                area_id=area.id,
                staff_id=staff.id
            )

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
            "deadline_passed": today.day > setting.deadline_day,
        }
    )