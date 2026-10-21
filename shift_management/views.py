from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from datetime import datetime,date
import calendar
from .models import ShiftAssignment
from .models import StaffProfile ,Store,ShiftTimeSlot,ManagementScope,ShiftSubmission,ShiftPreference
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

@staff_required
def staff_main(request):
    """一般ユーザーメイン画面"""
    context = get_calendar_data()
    
   
    shifts = ShiftAssignment.objects.filter(
        staff__user=request.user)
    


    context["staff_name"] = request.user.username
    context["shifts"] = shifts

        
    
    return render(
        request, 'shift_management/staff_main.html',
        context
                  )

@login_required(login_url="shift_management:staff_login")
def shift_submission(request):
    """シフト提出"""
    availability_list = []
    today = date.today()
    year = today.year
    month = today.month
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
                submission=submission_record,
                target_date=date(year, month, day),
                is_available=is_available,
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
    """管理者ユーザーメイン画面"""
    context = get_calendar_data()
    
   
    shifts = ShiftAssignment.objects.filter(
        staff__user=request.user)
    


    context["staff_name"] = request.user.username
    context["shifts"] = shifts

        
    
    return render(
        request, 'shift_management/manager_main.html',
        context
                  )
@manager_required
def edit_shift(request):
    """シフト編集画面"""
    today = date.today()
    now_month = today.month
    now_year = today.year
    stores = Store.objects.all()
    timeslots = ShiftTimeSlot.objects.all()
    
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
            shifts,
            id=shift_id
        )

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "delete" and selected_shift is not None:
            selected_shift.delete()
            return redirect("shift_management:edit_shift")

        form = ShiftAssignmentForm(
            request.POST,
            instance=selected_shift
        )

        if action == "save" and form.is_valid():
            shift = form.save(commit=False)
            shift.status = 1
            shift.save()
    else:
        form = ShiftAssignmentForm(
            instance=selected_shift,
            initial={
                "work_date": f"{display_year}-{display_month:02d}-{display_day:02d}"
            }
        )
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

@require_POST
def logout_view(request):
    logout(request)
    return redirect("shift_management:staff_login")