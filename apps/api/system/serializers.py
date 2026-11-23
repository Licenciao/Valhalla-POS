from rest_framework import serializers


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False, write_only=True)


class SessionRevocationSerializer(serializers.Serializer):
    reason = serializers.CharField(allow_blank=False, trim_whitespace=True)


class RestorationRequestSerializer(serializers.Serializer):
    backup_id = serializers.UUIDField()


class RestorationConfirmationSerializer(serializers.Serializer):
    confirmation_id = serializers.UUIDField()
    acknowledged = serializers.BooleanField(default=True)


class BackupExecuteSerializer(serializers.Serializer):
    simulate_drive = serializers.BooleanField(default=True, required=False)
