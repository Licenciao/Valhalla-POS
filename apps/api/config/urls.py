from django.urls import include, path
from django.views.generic import RedirectView

from system.errors import internal_error, not_found
from system.views import ApiRootView, WebAppView, WebAppChartJsView

urlpatterns = [
    path("", WebAppView.as_view(), name="web-app"),
    path("chart.umd.min.js", WebAppChartJsView.as_view(), name="web-app-chartjs"),
    path("api/v1/", ApiRootView.as_view(), name="api-root"),
    path("api/v1", RedirectView.as_view(url="/api/v1/", permanent=False)),
    path("api/v1/", include("system.urls")),
    path("api/v1/", include("catalog.urls")),
    path("api/v1/", include("devices.urls")),
]

handler404 = not_found
handler500 = internal_error
