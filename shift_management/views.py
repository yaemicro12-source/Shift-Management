from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from datetime import datetime,date
import calendar
from .models import ShiftAssignment
from .models import StaffProfile ,Store,ShiftTimeSlot
from django.http import HttpResponseBadRequest
from .forms import ShiftAssignmentForm,StaffProfileForm
from django.shortcuts import render, get_object_or_404, redirect

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

def shift_submission(request):
    """シフト提出"""
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
    return render(
        request,
        'shift_management/shift_submission.html',
        context
    )

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

    return render(
        request,'shift_management/staff_login.html',
    )

def manager_login(request):
    """管理者ログイン"""

    return render(
        request,"shift_management/manager_login.html",
    )

def all_staff(request):
    """従業員一覧"""
    staffs = StaffProfile.objects.all()


    context = {
        'staffs': staffs,
    }


    return render(
        request,"shift_management/all_staff.html",context
    )

def add_staff(request):
    """従業員追加"""
    if request.method == "POST":
        form = StaffProfileForm(request.POST)
        if form.is_valid():
                
                user = User.objects.create_user(
                username=form.cleaned_data["username"],
                password=form.cleaned_data["password"]
                )
                if User.objects.filter(username=user).exists():
                    #保存をしない

                #ユーザーに関連付ける
                staff = form.save(commit=False)
                staff.user = user
                staff.save()
                return redirect("shift_management:all_staff")
    else:
        form = StaffProfileForm()

    


    return render(
        request,"shift_management/add_staff.html",
    )

def edit_staff(request, staff_id):
    """従業員編集"""
    staff = StaffProfile.objects.get(id=staff_id)

    context = {
        'staff': staff,
    }

    return render(
        request,
        'shift_management/edit_staff.html',
        context
    )