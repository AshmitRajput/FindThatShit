from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.exceptions import NotFound

from accounts.permissions import IsOwner
from resources.serializers import (
    ResourceCreateSerializer,
    ResourceDetailSerializer,
    ResourceListQuerySerializer,
    ResourceSummarySerializer,
    ResourceUpdateSerializer,
    ProcessRequestSerializer,
    UploadUrlRequestSerializer,
)
from resources.models import Resource
from resources.services import idempotency_service, processing_service, resource_service, storage_service


class ResourceViewSet(viewsets.GenericViewSet):
    """Thin controller: validate input, call a service, shape the response."""

    permission_classes = [IsAuthenticated, IsOwner]
    lookup_value_converter = "uuid"
    throttle_scope = None  # set per action; declared here so @action(throttle_scope=...) is accepted

    def get_queryset(self):
        return resource_service.base_queryset(self.request.user)  # never query Resource directly here

    def list(self, request):
        query = ResourceListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        queryset = resource_service.list_resources_for_user(request.user, query.validated_data)
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(ResourceSummarySerializer(page, many=True).data)

    def create(self, request):
        serializer = ResourceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        def perform():
            resource = resource_service.create_resource(request.user, serializer.validated_data)
            return status.HTTP_201_CREATED, resource.pk, None
        _, _, resource_id, _ = idempotency_service.run(
            request.user, idempotency_service.get_key(request), "POST /resources",
            idempotency_service.fingerprint(serializer.validated_data), perform,
        )
        try:
            resource = resource_service.get_resource_for_user(request.user, resource_id)
        except Resource.DoesNotExist as exc:
            raise NotFound() from exc
        return Response(ResourceSummarySerializer(resource).data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        return Response(ResourceDetailSerializer(self.get_object()).data)

    def partial_update(self, request, pk=None):
        resource = self.get_object()
        serializer = ResourceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = resource_service.update_resource(request.user, resource, serializer.validated_data)
        return Response(ResourceDetailSerializer(updated).data)

    def destroy(self, request, pk=None):
        resource_service.delete_resource(self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(
        detail=True, methods=["post"], url_path="upload-url",
        throttle_classes=[ScopedRateThrottle], throttle_scope="upload",
    )
    def upload_url(self, request, pk=None):
        serializer = UploadUrlRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = storage_service.create_upload_for_resource(
            self.get_object(), data["filename"], data["mime_type"], data["size"]
        )
        return Response(result)

    @action(detail=True, methods=["get"], url_path="download-url")
    def download_url(self, request, pk=None):
        return Response(storage_service.create_download_for_resource(self.get_object()))

    @action(
        detail=True, methods=["post"], url_path="process",
        throttle_classes=[ScopedRateThrottle], throttle_scope="process",
    )
    def process(self, request, pk=None):
        resource = self.get_object()
        serializer = ProcessRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        force = serializer.validated_data["force"]
        processing_service.check_processable(resource)

        def perform():
            job = processing_service.request_processing(resource, force)
            return status.HTTP_202_ACCEPTED, resource.pk, job.pk
        _, _, _, job_id = idempotency_service.run(
            request.user, idempotency_service.get_key(request),
            f"POST /resources/{resource.pk}/process",
            idempotency_service.fingerprint({"force": force}), perform,
        )
        job = processing_service.get_job_for_user(request.user, job_id)
        if job is None:
            raise NotFound()
        return Response({
            "job_id": str(job.pk), "resource_id": str(job.resource_id),
            "processing_status": job.resource.processing_status,
        }, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["get"], url_path="processing")
    def processing(self, request, pk=None):
        return Response(processing_service.processing_status_for(self.get_object()))