from rest_framework.routers import SimpleRouter

from resources.views import ResourceViewSet

router = SimpleRouter(trailing_slash=False, use_regex_path=False)
router.register("resources", ResourceViewSet, basename="resource")

urlpatterns = router.urls