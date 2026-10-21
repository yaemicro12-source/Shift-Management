"""shift_managementのURLパターンの定義"""

from django.urls import path

from . import views

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
    path('logout/',views.logout_view,name='logout'),
]