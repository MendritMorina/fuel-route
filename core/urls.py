from django.urls import include, path

from api.views import HealthView

urlpatterns = [
    path("health/", HealthView.as_view()),
    path("api/", include("api.urls")),
]
