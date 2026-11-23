"""
devices/urls.py

Rutas URL para los endpoints de Dispositivos, Emparejamiento QR, Sincronización y Conflictos.
Montado bajo /api/v1/ en config/urls.py.
"""

from django.urls import path

from devices.views import (
    ConflictResolutionView,
    ConflictsPageView,
    DeviceEventBatchView,
    DeviceQuotaView,
    DeviceRevocationView,
    DevicesPageView,
    PairingCompleteView,
    PairingRequestCreateView,
)

urlpatterns = [
    # Emparejamiento
    path("pairing-requests", PairingRequestCreateView.as_view(), name="pairing_request_create"),
    path("pairing-requests/<uuid:pairing_request_id>/complete", PairingCompleteView.as_view(), name="pairing_complete"),

    # Dispositivos y Cuotas
    path("devices/pages/<str:cursor>", DevicesPageView.as_view(), name="devices_page"),
    path("devices/<str:device_id>/revocations", DeviceRevocationView.as_view(), name="device_revocation"),
    path("devices/<str:device_id>/quota", DeviceQuotaView.as_view(), name="device_quota"),

    # Lotes de Eventos Sincronizados (Firma ECDSA)
    path("devices/<str:device_id>/event-batches", DeviceEventBatchView.as_view(), name="device_event_batches"),

    # Conflictos
    path("conflicts/pages/<str:cursor>", ConflictsPageView.as_view(), name="conflicts_page"),
    path("conflicts/<uuid:event_id>/resolutions", ConflictResolutionView.as_view(), name="conflict_resolution"),
]
