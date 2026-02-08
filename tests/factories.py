"""
Factory Boy factories for model instances.
"""
import factory
from django.utils import timezone

from monitoring.models import HealthCheck, Message


class HealthCheckFactory(factory.django.DjangoModelFactory):
    """Factory for HealthCheck model."""

    class Meta:
        model = HealthCheck

    service_name = factory.Sequence(lambda n: f"service-{n}")
    status = "up"
    response_time_ms = factory.Faker("random_int", min=50, max=500)
    status_code = 200
    error_message = ""
    checked_at = factory.LazyFunction(timezone.now)


class MessageFactory(factory.django.DjangoModelFactory):
    """Factory for Message model."""

    class Meta:
        model = Message

    severity = "medium"
    text = factory.Faker("sentence", nb_words=10)
    active = True
