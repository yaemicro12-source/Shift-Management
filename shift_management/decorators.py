from django.shortcuts import redirect
from django.core.exceptions import PermissionDenied
from functools import wraps

def manager_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        # ここでログイン状態と権限を確認する
        permission =request.user.is_authenticated
        if not permission:
            return redirect("shift_management:manager_login")
        else:
            staff = request.user.staffprofile
            if staff.role != 2:
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

    return wrapper

def staff_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("shift_management:staff_login")

        staff = request.user.staffprofile

        if staff.role != 1:
            raise PermissionDenied

        return view_func(request, *args, **kwargs)

    return wrapper