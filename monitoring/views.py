"""
Views for the status page.
"""
from django.views.decorators.cache import cache_page
from django.shortcuts import render


@cache_page(30)
def status_page(request):
    """
    Public status page showing service health and incidents.
    """
    # TODO: Implement in Phase 4
    return render(request, "monitoring/status.html", {})
