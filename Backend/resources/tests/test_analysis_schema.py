from django.test import SimpleTestCase

from resources.providers.ai import AIProviderError
from resources.services.analysis_schema import validate_analysis

VALID = {
    "title": "  My Resume  ", "resource_type": "pdf", "resource_description": " A resume. ",
    "source_description": "", "summary": "S", "keywords": ["Resume", "resume", " job  search "],
    "tags": ["Career", "career", 5], "language": " EN ",
}


class ValidateAnalysisTests(SimpleTestCase):
    def test_valid_output_is_cleaned(self):
        result = validate_analysis(dict(VALID))
        self.assertEqual(result["title"], "My Resume")
        self.assertEqual(result["resource_type"], "PDF")
        self.assertEqual(result["resource_description"], "A resume.")
        self.assertEqual(result["keywords"], ["Resume", "job search"])
        self.assertEqual(result["tags"], ["Career"])
        self.assertEqual(result["language"], "en")

    def test_unknown_resource_type_becomes_empty(self):
        self.assertEqual(validate_analysis({**VALID, "resource_type": "SPACESHIP"})["resource_type"], "")

    def test_caps_are_applied(self):
        result = validate_analysis({**VALID, "title": "x" * 500, "keywords": [f"k{i}" for i in range(100)],
                                    "tags": [f"t{i}" for i in range(50)]})
        self.assertEqual(len(result["title"]), 255)
        self.assertEqual(len(result["keywords"]), 30)
        self.assertEqual(len(result["tags"]), 10)

    def test_invalid_output_is_a_retryable_error(self):
        bad_inputs = [
            "not a dict", [], {**VALID, "resource_description": ""}, {**VALID, "resource_description": None},
            {**VALID, "keywords": "resume"}, {**VALID, "tags": {"a": 1}}, {**VALID, "title": 12},
        ]
        for raw in bad_inputs:
            with self.assertRaises(AIProviderError) as ctx:
                validate_analysis(raw)
            self.assertEqual(ctx.exception.error_code, "LLM_INVALID_OUTPUT")
            self.assertTrue(ctx.exception.retryable)
