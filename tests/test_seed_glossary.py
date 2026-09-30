from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sjtu_learning_assistant.seed_glossary import (
    DOMAIN_IDS,
    MIN_TERMS_PER_PACK,
    SCHEMA_VERSION,
    SeedGlossaryValidationError,
    estimate_tokens,
    load_pack,
    load_seed_packs,
    retrieve_seed_glossary,
    select_domains,
    validate_pack,
)


class SeedGlossaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.packs = load_seed_packs()

    def test_loads_all_six_packs_in_stable_order(self) -> None:
        self.assertEqual(DOMAIN_IDS, tuple(pack.pack_id for pack in self.packs))
        self.assertTrue(all(pack.schema_version == SCHEMA_VERSION for pack in self.packs))
        self.assertTrue(all(pack.pack_version == "1.0.0" for pack in self.packs))

    def test_each_pack_has_at_least_24_terms_and_total_is_at_least_144(self) -> None:
        counts = [len(pack.terms) for pack in self.packs]
        self.assertTrue(all(count >= MIN_TERMS_PER_PACK for count in counts))
        self.assertGreaterEqual(sum(counts), 144)

    def test_schema_fields_sources_and_content_are_complete(self) -> None:
        for pack in self.packs:
            self.assertTrue(pack.display_name)
            self.assertTrue(pack.domain_keywords)
            self.assertIn("仅作为术语参考", pack.source_note)
            source_ids = {source.source_id for source in pack.sources}
            for source in pack.sources:
                self.assertTrue(source.title)
                self.assertTrue(source.url.startswith("https://"))
                self.assertEqual("seed", source.origin)
            for term in pack.terms:
                self.assertTrue(term.id.startswith(pack.pack_id + "."))
                self.assertTrue(term.canonical.isascii())
                self.assertTrue(term.zh_name)
                self.assertIsInstance(term.aliases, tuple)
                self.assertTrue(term.category)
                self.assertTrue(term.definition_zh)
                self.assertIsInstance(term.asr_variants, tuple)
                self.assertIn(term.source_id, source_ids)
                self.assertEqual(0, term.source_priority)

    def test_ids_are_globally_unique_and_stable(self) -> None:
        first = [term.id for pack in self.packs for term in pack.terms]
        second = [term.id for pack in load_seed_packs() for term in pack.terms]
        self.assertEqual(len(first), len(set(first)))
        self.assertEqual(first, second)
        self.assertIn("traditional_machine_learning.ols_linear_regression", first)
        self.assertIn("accounting.double_entry_accounting", first)

    def test_strict_schema_rejects_unknown_fields(self) -> None:
        resource = (
            Path(__file__).parents[1]
            / "resources"
            / "seed_glossary"
            / "marketing.json"
        )
        payload = json.loads(resource.read_text(encoding="utf-8"))
        payload["unexpected"] = "must fail closed"
        with self.assertRaisesRegex(SeedGlossaryValidationError, "unknown fields"):
            validate_pack(payload)

    def test_filename_must_match_pack_id(self) -> None:
        resource = (
            Path(__file__).parents[1]
            / "resources"
            / "seed_glossary"
            / "marketing.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            renamed = Path(temporary) / "accounting.json"
            renamed.write_bytes(resource.read_bytes())
            with self.assertRaisesRegex(SeedGlossaryValidationError, "filename"):
                load_pack(renamed)

    def test_domain_selection_uses_metadata_and_current_text(self) -> None:
        accounting = select_domains(
            self.packs,
            {"course_name": "Financial Accounting", "description": "借贷记账与财务报表"},
            "Today we explain the accounting equation.",
        )
        regression = select_domains(
            self.packs,
            {"course_name": "Business Statistics"},
            "ordinary least squares estimates a regression equation and residuals",
        )
        self.assertEqual("accounting", accounting[0])
        self.assertEqual("regression_analysis", regression[0])

    def test_relevance_ranking_puts_text_matches_first(self) -> None:
        result = retrieve_seed_glossary(
            {"course_name": "Regression Analysis"},
            "OLS minimizes the sum of squared errors; inspect each residual.",
            packs=self.packs,
            max_terms=4,
            max_chars=4000,
            max_tokens=2000,
        )
        selected = {term.canonical for term in result.terms}
        self.assertIn("ordinary least squares", selected)
        self.assertIn("sum of squared errors", selected)
        self.assertIn("residual", selected)
        self.assertEqual(("regression_analysis",), result.domains)
        self.assertTrue(result.sources)
        self.assertTrue(all(source.source_id in {term.source_id for term in result.terms}
                            for source in result.sources))

    def test_unrelated_course_does_not_inject_any_domain(self) -> None:
        result = retrieve_seed_glossary(
            {"course_name": "中国古代文学", "description": "唐诗与宋词鉴赏"},
            "本节讨论诗歌意象、格律与作者生平。",
            packs=self.packs,
        )
        self.assertEqual((), result.domains)
        self.assertEqual((), result.terms)
        self.assertEqual("", result.as_prompt_context())

    def test_retrieval_is_idempotent_and_does_not_mutate_packs(self) -> None:
        before = self.packs
        kwargs = {
            "course_metadata": {"course_name": "Principles of Marketing"},
            "current_text": "Use market segmentation to define a target market.",
            "packs": self.packs,
            "max_terms": 8,
            "max_chars": 2000,
            "max_tokens": 800,
        }
        first = retrieve_seed_glossary(**kwargs)
        second = retrieve_seed_glossary(**kwargs)
        self.assertEqual(first, second)
        self.assertEqual(before, self.packs)

    def test_character_token_and_term_budgets_are_all_enforced(self) -> None:
        result = retrieve_seed_glossary(
            {"course_name": "Artificial Intelligence"},
            "AI system trustworthy AI generative AI large language model",
            packs=self.packs,
            max_terms=3,
            max_chars=420,
            max_tokens=125,
        )
        context = result.as_prompt_context()
        self.assertLessEqual(len(result.terms), 3)
        self.assertEqual(len(context), result.char_count)
        self.assertEqual(estimate_tokens(context), result.estimated_tokens)
        self.assertLessEqual(result.char_count, 420)
        self.assertLessEqual(result.estimated_tokens, 125)

    def test_course_memory_overrides_conflicting_seed_and_keeps_provenance(self) -> None:
        course_glossary = {
            "course-logistic-regression": {
                "item_id": "course-logistic-regression",
                "value": {
                    "canonical": "logistic regression",
                    "zh_name": "逻辑斯谛回归",
                    "aliases": ["logit model"],
                    "category": "official_course_term",
                    "definition_zh": "本课程材料采用的名称与定义。",
                },
                "source": "official_course_material",
            }
        }
        result = retrieve_seed_glossary(
            {"domain": "traditional_machine_learning"},
            "logistic regression and logit model",
            packs=self.packs,
            course_glossary=course_glossary,
            max_terms=8,
            max_chars=3000,
            max_tokens=1200,
        )
        matching = [term for term in result.terms if term.canonical == "logistic regression"]
        self.assertEqual(1, len(matching))
        self.assertEqual("course_memory", matching[0].origin)
        self.assertEqual("逻辑斯谛回归", matching[0].zh_name)
        self.assertNotIn(
            "traditional_machine_learning.logistic_regression",
            {term.id for term in result.terms},
        )
        source = next(source for source in result.sources
                      if source.source_id == "course_memory:official_course_material")
        self.assertEqual("course_memory", source.origin)
        self.assertIsNone(source.url)

    def test_cross_pack_aliases_are_deduplicated(self) -> None:
        result = retrieve_seed_glossary(
            {"course_name": "Machine Learning Regression Analysis"},
            "regression estimates a continuous target",
            packs=self.packs,
            domains=["traditional_machine_learning", "regression_analysis"],
            max_terms=48,
            max_chars=12000,
            max_tokens=5000,
        )
        regression_forms = [
            term for term in result.terms
            if "regression" in {form.casefold() for form in term.lookup_forms}
        ]
        self.assertEqual(1, len(regression_forms))
        self.assertEqual(len(result.terms), len({term.id for term in result.terms}))

    def test_explicit_domain_selection_is_deduplicated(self) -> None:
        result = retrieve_seed_glossary(
            {},
            "",
            packs=self.packs,
            domains=["marketing", "marketing"],
            max_terms=2,
            max_chars=1000,
            max_tokens=500,
        )
        self.assertEqual(("marketing",), result.domains)
        self.assertEqual(len(result.terms), len({term.id for term in result.terms}))


if __name__ == "__main__":
    unittest.main()
