from rest_framework.pagination import PageNumberPagination
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "limit"
    max_page_size = 50

    def get_paginated_response(self, data):
        return Response(
            {
                "count": self.page.paginator.count,
                "page": self.page.number,
                "limit": self.page.paginator.per_page,
                "results": data,
            }
        )

    def get_page_size(self, request):
        raw_limit = request.query_params.get(self.page_size_query_param)
        if raw_limit is not None:
            try:
                limit = int(raw_limit)
            except ValueError as exc:
                raise ValidationError({"limit": ["A valid integer is required."]}) from exc
            if limit < 1:
                raise ValidationError({"limit": ["Ensure this value is greater than 0."]})
        return super().get_page_size(request)

    def get_page_number(self, request, paginator):
        raw_page = request.query_params.get(self.page_query_param)
        if raw_page is not None:
            try:
                page = int(raw_page)
            except ValueError as exc:
                raise ValidationError({"page": ["A valid integer is required."]}) from exc
            if page < 1:
                raise ValidationError({"page": ["Ensure this value is greater than 0."]})
        return super().get_page_number(request, paginator)
