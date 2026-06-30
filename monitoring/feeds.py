"""
Syndication feeds for the status page.

Exposes the incident history as RSS and Atom so people can subscribe in a
feed reader without an account — a standard status-page affordance. Built on
Django's syndication framework, so there is no extra dependency.
"""
from django.conf import settings
from django.contrib.syndication.views import Feed
from django.urls import reverse
from django.utils.feedgenerator import Atom1Feed

from monitoring.models import Message


class IncidentFeed(Feed):
    """RSS 2.0 feed of incident messages (most recent first)."""

    title = "Sefaria Status — Incident History"
    description = "Incidents and status updates for Sefaria's services."

    def link(self):
        # Link back to the status page (absolute via STATUS_PAGE_URL).
        return getattr(settings, "STATUS_PAGE_URL", "/")

    def items(self):
        return Message.objects.order_by("-updated_at")[:30]

    def item_title(self, item: Message) -> str:
        return f"[{item.get_severity_display()}] {item.text[:80]}"

    def item_description(self, item: Message) -> str:
        return item.text

    def item_pubdate(self, item: Message):
        return item.created_at

    def item_updateddate(self, item: Message):
        return item.updated_at

    def item_link(self, item: Message) -> str:
        # There is no per-incident page; link to the status page.
        return getattr(settings, "STATUS_PAGE_URL", "/")

    # Each incident needs a stable, unique id that is not a permalink (we have
    # no per-incident URL), otherwise every item would share the page's link.
    item_guid_is_permalink = False

    def item_guid(self, item: Message) -> str:
        return f"sefaria-status-incident-{item.pk}"


class AtomIncidentFeed(IncidentFeed):
    """Atom 1.0 version of the same incident feed."""

    feed_type = Atom1Feed
    subtitle = IncidentFeed.description
