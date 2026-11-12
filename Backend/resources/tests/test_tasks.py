from unittest import mock

from django.test import SimpleTestCase

from resources import tasks
from resources.services.processing_service import RetryableProcessingError


class ProcessResourceTaskTests(SimpleTestCase):
    def test_first_attempt_is_not_final(self):
        with mock.patch.object(tasks.processing_service, "run_job") as run_job:
            tasks.process_resource.apply(args=["job-1"])
        run_job.assert_called_once_with("job-1", final_attempt=False)

    def test_last_retry_is_final(self):
        with mock.patch.object(tasks.processing_service, "run_job") as run_job:
            tasks.process_resource.apply(args=["job-1"], retries=3)
        run_job.assert_called_once_with("job-1", final_attempt=True)

    def test_retryable_error_triggers_backoff_retry(self):
        error = RetryableProcessingError("PROVIDER_TIMEOUT", "slow")
        with mock.patch.object(tasks.processing_service, "run_job", side_effect=error), mock.patch.object(
            tasks.process_resource, "retry", side_effect=RuntimeError("retry requested")
        ) as retry:
            tasks.process_resource.apply(args=["job-1"], retries=2)
        self.assertEqual(retry.call_args.kwargs["countdown"], 120)
        self.assertIs(retry.call_args.kwargs["exc"], error)

    def test_no_retry_when_the_service_finishes_or_fails_quietly(self):
        with mock.patch.object(tasks.processing_service, "run_job", return_value=None), mock.patch.object(
            tasks.process_resource, "retry"
        ) as retry:
            tasks.process_resource.apply(args=["job-1"])
        retry.assert_not_called()

    def test_task_is_registered_with_three_retries(self):
        self.assertEqual(tasks.process_resource.max_retries, 3)
