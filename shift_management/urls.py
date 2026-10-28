from .staff_requests import shortage_request
"""shift_managementのURLパターンの定義"""

from django.urls import path

from . import views
from .shift_controls import shift_management_page
from .request_popup import respond_request
from .messaging import message_page
from .day_views import day_shift_detail

app_name = 'shift_management'

urlpatterns = [
    path('staff_main/', views.staff_main, name='staff_main'),
    path('shift_submission/',views.shift_submission,name='sift_submission'),
    path('manager_main/',views.manager_main,name='manager_main'),
    path('edit_shift/',views.edit_shift,name='edit_shift'),
    path('staff_login/',views.staff_login,name='staff_login'),
    path('manager_login/',views.manager_login,name="manager_login"),
    path('all_staff/',views.all_staff,name='all_staff'),
    path('add_staff/',views.add_staff,name='add_staff'),
    path(
        'edit_staff/<int:staff_id>/',
        views.edit_staff,
        name='edit_staff'
    ),
    path(
        "shortage/<int:shortage_id>/request/",
        shortage_request,
        name="shortage_request",
    ),
    path('logout/',views.logout_view,name='logout'),
    path(
        'evaluations/',
        views.evaluation_list,
        name='evaluation_list'
    ),
    path(
        "settings/evaluation/",
        views.evaluation_settings,
        name="evaluation_settings"
    ),
    path(
        "settings/evaluation/<int:area_id>/items/",
        views.evaluation_items,
        name="evaluation_items"
    ),
    path(
        "settings/",
        views.system_settings,
        name="system_settings"
    ),
    path(
        "evaluations/<int:area_id>/<int:staff_id>/",
        views.evaluation_detail,
        name="evaluation_detail"
    ),
    path(
        "generate_shift/",
        views.generate_shift,
        name="generate_shift",
    ),
    path(
        "shift_management/",
        shift_management_page,
        name="shift_management",
    ),
    path(
    "requests/<int:request_id>/respond/",
    respond_request,
    name="respond_request",
    ),
    path(
    "message/",
    message_page,
    name="message",
    ),
    path(
        "message/<int:staff_id>/",
        message_page,
        name="conversation",
    ),
    path(
        "shifts/day/<int:year>/<int:month>/<int:day>/",
        day_shift_detail,
        name="day_shift_detail",
    ),
]