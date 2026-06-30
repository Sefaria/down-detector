"""
URL configuration for monitoring app.
"""
from django.urls import path

from . import views

app_name = "monitoring"

urlpatterns = [
    path("", views.status_page, name="status"),
    path("api/status/", views.status_api, name="status_api"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("sitemap.xml", views.sitemap_xml, name="sitemap_xml"),
]
