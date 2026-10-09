from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(help_text="Start location (e.g., 'New York, NY')")
    finish = serializers.CharField(help_text="Finish location (e.g., 'Los Angeles, CA')")
