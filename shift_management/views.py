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
def generate_shift(request):
    today = date.today()
    candidates = []  # 勤務可能なスタッフの希望データ

    if today.month == 12:
        year = today.year + 1
        month = 1
    else:
        year = today.year
        month = today.month + 1

    submissions = ShiftSubmission.objects.filter(
        is_latest=True,
        month=month,
        year=year
    )

    for submission in submissions:
        preferences = ShiftPreference.objects.filter(submission=submission)

        for preference in preferences:
            if preference.is_available:
                candidates.append(preference)

    # スタッフごとの勤務時間を管理
    weekly_minutes = {}
    monthly_minutes = {}
    # 対象月の初日
    month_start = date(year, month, 1)

    # 前月末の5日間を取得
    previous_start = month_start - timedelta(days=5)

    working_days = {}

    # 前月の確定シフトと対象月の既存シフトを取得
    existing_shifts = ShiftAssignment.objects.filter(
        work_date__gte=previous_start,
        work_date__lt=month_start,
        status=2
    )

    for shift in existing_shifts:
        working_days.setdefault(
            shift.staff_id, set()
        ).add(shift.work_date)


    # 来月の最終日を取得
    last_day = calendar.monthrange(year, month)[1]

    # 来月1日から月末まで繰り返す
    for day in range(1, last_day + 1):
        target_date = date(year, month, day)

        # 月曜日を週の開始日とする
        week_start = target_date - timedelta(
            days=target_date.weekday()
        )
        week_end = week_start + timedelta(
            days= 6
        )

        # その曜日の必要人数ルールを取得
        rules = RequiredStaffingRule.objects.filter(
            weekday=target_date.weekday()
        )

        assigned_staff = set()

        for rule in rules:
            canworkers = []  # 条件に一致したスタッフ

            # シフト枠の勤務時間を分単位で計算

            end_datetime = datetime.combine(
                target_date,
                rule.shift_time_slot.end_time
            )
            start_datetime = datetime.combine(
                target_date,
                rule.shift_time_slot.start_time
            )

            work_minutes = int(
                (end_datetime - start_datetime).total_seconds() / 60
            )

            # その日に勤務できるスタッフを確認
            for candidate in candidates:

                # 希望日とシフトを組む日付が一致するか
                if candidate.target_date == target_date:

                    # 希望開始時間がシフト開始時間以前か
                    if (
                        candidate.available_start_time is not None
                        and candidate.available_end_time is not None
                        and candidate.available_start_time
                        <= rule.shift_time_slot.start_time
                    ):

                        # 希望終了時間がシフト終了時間以降か
                        if (
                            candidate.available_end_time
                            >= rule.shift_time_slot.end_time
                        ):

                            # 対象店舗に所属しているか
                            if StaffStoreAssignment.objects.filter(
                                staff=candidate.submission.staff,
                                store=rule.store
                            ).filter(
                                Q(starts_on__lte=target_date)
                                | Q(starts_on__isnull=True),
                                Q(ends_on__gte=target_date)
                                | Q(ends_on__isnull=True)
                            ).exists():

                                staff = candidate.submission.staff

                                # 同じ日の重複配置を防ぐ
                                if staff not in assigned_staff:

                                    # スタッフの勤務時間上限
                                    weekly_limit = staff.weekly_max_minutes
                                    monthly_limit = staff.monthly_max_minutes

                                    # 現在の勤務時間に今回のシフトを加算
                                    week_key = (staff.id, week_start)
                                    month_key = staff.id

                                    weekly_total = (
                                        weekly_minutes.get(week_key, 0)
                                        + work_minutes
                                    )
                                    monthly_total = (
                                        monthly_minutes.get(month_key, 0)
                                        + work_minutes
                                    )

                                    # 週の勤務時間上限を確認
                                    if (
                                        weekly_limit is not None
                                        and weekly_total > weekly_limit
                                    ):
                                        continue

                                    # 月の勤務時間上限を確認
                                    if (
                                        monthly_limit is not None
                                        and monthly_total > monthly_limit
                                    ):
                                        continue
                                    
                                    # 週5日までの勤務制限
                                    weekly_work_days = sum(
                                        1
                                        for work_day in working_days.get(staff.id, set())
                                        if week_start <= work_day <= week_end
                                    )

                                    if weekly_work_days >= 5:
                                        continue

                                    # 月をまたいだ連続勤務も確認
                                    consecutive_days = 0
                                    check_date = target_date - timedelta(days=1)

                                    while check_date in working_days.get(staff.id, set()):
                                        consecutive_days += 1
                                        check_date -= timedelta(days=1)

                                    # 6連勤を禁止
                                    if consecutive_days >= 5:
                                        continue
                                    

                                    canworkers.append(staff)

                                    
            # 必要人数を上限としてスタッフを選択
            selected_workers = canworkers[:rule.required_count]

            for worker in selected_workers:
                assigned_staff.add(worker)
                working_days.setdefault(worker.id, set()).add(target_date)
                # 選ばれたスタッフの勤務時間を加算
                week_key = (worker.id, week_start)
                month_key = worker.id

                weekly_minutes[week_key] = (
                    weekly_minutes.get(week_key, 0)
                    + work_minutes
                )

                monthly_minutes[month_key] = (
                    monthly_minutes.get(month_key, 0)
                    + work_minutes
                )
                ShiftAssignment.objects.create(
                    staff= worker,
                    store = rule.store,
                    work_date = target_date,
                    shift_time_slot = rule.shift_time_slot,
                    status = 1
                )

            # 人数不足を計算
            if len(selected_workers) < rule.required_count:
                shortage = (
                    rule.required_count - len(selected_workers)
                )
                ShiftShortage.objects.create(
                    store = rule.store,
                    target_date = target_date,
                    shift_time_slot = rule.shift_time_slot,
                    required_count = rule.required_count,
                    assigned_count = rule.required_count - shortage
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