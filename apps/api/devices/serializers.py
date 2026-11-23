"""
devices/serializers.py

Serializadores DRF para emparejamiento, dispositivos, cuotas y sincronización.
Cumple con los esquemas de 11-borrador-contrato-api.md §§28-36.
"""

from decimal import Decimal
from rest_framework import serializers


class PairingRequestCreateSerializer(serializers.Serializer):
    device_label = serializers.CharField(max_length=150)


class PairingCompleteSerializer(serializers.Serializer):
    qr_token = serializers.CharField(max_length=200)
    public_key_spki = serializers.CharField()
    key_id = serializers.CharField(max_length=100)
    protocol_version = serializers.CharField(max_length=10, required=False, default="v1")


class DeviceRevocationSerializer(serializers.Serializer):
    reason = serializers.CharField()


class QuotaComponentInputSerializer(serializers.Serializer):
    component_id = serializers.IntegerField(min_value=1)
    quantity = serializers.DecimalField(max_digits=18, decimal_places=6, min_value=Decimal("0.000000"))
    unit = serializers.CharField(max_length=20, required=False, default="")


class DeviceQuotaUpdateSerializer(serializers.Serializer):
    quota_version = serializers.IntegerField(min_value=1)
    components = QuotaComponentInputSerializer(many=True, allow_empty=False)


class SyncEventItemSerializer(serializers.Serializer):
    event_id = serializers.UUIDField()
    sequence = serializers.IntegerField(min_value=1)
    type = serializers.CharField(max_length=50)
    occurred_at = serializers.CharField(required=False, default="")
    protocol_version = serializers.CharField(max_length=10, required=False, default="v1")
    payload = serializers.DictField()


class EventBatchSerializer(serializers.Serializer):
    events = SyncEventItemSerializer(many=True, allow_empty=False)


class ConflictResolutionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["ACCEPT", "REJECT"])
    reason = serializers.CharField()
