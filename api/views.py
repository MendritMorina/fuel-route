from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer
from .services import RouteError, plan


class HealthView(APIView):
    def get(self, request: Request) -> Response:
        return Response({"status": "ok"})


class RoutePlanView(APIView):
    def post(self, request: Request) -> Response:
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = plan(
                serializer.validated_data["start"],
                serializer.validated_data["finish"],
            )
        except RouteError as exc:
            return Response({"error": str(exc)}, status=exc.status)
        return Response(result, status=status.HTTP_200_OK)
