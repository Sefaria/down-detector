"""
URL configuration for monitoring app.
"""
from django.urls import path

from . import views

app_name = "monitoring"

urlpatterns = [
    path("", views.status_page, name="status"),
]
