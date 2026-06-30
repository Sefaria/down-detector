"""
URL configuration for monitoring app.
"""
from django.urls import path

from . import views
from .feeds import IncidentFeed, AtomIncidentFeed

app_name = "monitoring"

urlpatterns = [
    path("", views.status_page, name="status"),
    path("healthz", views.healthz, name="healthz"),
    path("api/status/", views.status_api, name="status_api"),
    path("history.rss", IncidentFeed(), name="incident_feed_rss"),
    path("history.atom", AtomIncidentFeed(), name="incident_feed_atom"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("sitemap.xml", views.sitemap_xml, name="sitemap_xml"),
]
