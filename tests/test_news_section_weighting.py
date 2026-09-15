"""Offline checks for actual selection, XML extraction and ranking provenance."""
from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from Agent_Team.News_Agent.ranking.section_weighting import (
    WEIGHTS, SECTION_TITLES, POLICY_VERSION, aggregate, configured_weights,
    dense_scores, extract_section_chunks, section_indices,
)
from Agent_Team.News_Agent.pipelines import run_news_pipeline as pipeline
from Agent_Team.News_Agent.pipelines.build_corporate_context_db import build_context_db
from Agent_Team.News_Agent.dart.schemas import RawNewsRecord
from Agent_Team.News_Agent.io.storage import save_jsonl


def report_xml(*, unclosed=False, consolidated=True):
    sections = "".join(
        f"<SECTION-2><TITLE>{i}. {title}</TITLE><P>구간-{key} 100억원"
        f"{'' if unclosed else '</P>'}</SECTION-2>"
        for i, (key, title) in enumerate(SECTION_TITLES.items(), 1)
    )
    return (
        f"<ROOT>{sections}<TABLE-GROUP><TITLE>2-2. {'연결 ' if consolidated else ''}포괄손익계산서</TITLE>"
        "<TABLE><TR><TH>항목</TH><TH>2025</TH></TR>"
        "<TR><TE>영업이익</TE><TE>(123,456)</TE></TR></TABLE></TABLE-GROUP></ROOT>"
    )


class SectionWeightingTests(unittest.TestCase):
    def test_default_configuration_and_validation(self):
        config = yaml.safe_load((ROOT / "configs/news_default.yaml").read_text())
        self.assertEqual(config["news"]["weekly_top_k"], 3)
        self.assertNotIn("weekly_embedding_candidates", config["news"])
        self.assertNotIn("weekly_rerank_top_k", config["news"])
        self.assertNotIn("reranker_model_name", config["models"])
        self.assertIsNone(config["news"]["monthly_top_k"])
        self.assertEqual(config["news"]["raw_news_policy"], "monthly_selected_articles_v1")
        self.assertEqual(configured_weights(config), WEIGHTS)
        self.assertIsNone(configured_weights({}))
        self.assertAlmostEqual(aggregate({key: float(key == "sales") for key in WEIGHTS}, WEIGHTS), .3)
        for weights in ({**WEIGHTS, "sales": -.3}, {"sales": 1}, {**WEIGHTS, "sales": float("nan")}):
            with self.assertRaises(ValueError):
                aggregate({key: 0 for key in WEIGHTS}, weights)
        with self.assertRaises(ValueError):
            aggregate({key: [float("nan")] for key in WEIGHTS}, WEIGHTS)

    def test_xml_sections_and_negative_income_cells(self):
        for consolidated in (True, False):
            chunks = extract_section_chunks(report_xml(consolidated=consolidated),
                                            company="기업", report_date="20250814")
            self.assertEqual({chunk["section"] for chunk in chunks}, set(WEIGHTS))
            income = " ".join(c["text"] for c in chunks if c["section"] == "earnings")
            for text in ("영업이익", "(123,456)", "2025"):
                self.assertIn(text, income)
        with self.assertRaises(ValueError):
            extract_section_chunks("<ROOT/>", company="기업", report_date="20250814")

    def test_unclosed_inline_cannot_swallow_following_sections(self):
        chunks = extract_section_chunks(report_xml(unclosed=True), company="기업", report_date="20250814")
        sales = " ".join(c["text"] for c in chunks if c["section"] == "sales")
        self.assertNotIn("구간-products", sales)
        self.assertNotIn("영업이익", sales)

    def test_consolidated_income_preferred_over_comprehensive_and_separate(self):
        xml = report_xml().replace("</ROOT>", (
            "<TABLE-GROUP><TITLE>2-2. 연결 손익계산서</TITLE><P>연결영업이익 300</P></TABLE-GROUP>"
            "<TABLE-GROUP><TITLE>4-2. 손익계산서</TITLE><P>별도영업이익 900</P></TABLE-GROUP></ROOT>"
        ))
        chunks = extract_section_chunks(xml, company="기업", report_date="20250814")
        income = " ".join(c["text"] for c in chunks if c["section"] == "earnings")
        self.assertIn("연결영업이익 300", income)
        self.assertNotIn("별도영업이익", income)
        self.assertNotIn("(123,456)", income)

    def test_context_policy_rejects_old_or_missing_sections(self):
        rows = [{"section_type": key, "ranking_policy": POLICY_VERSION} for key in WEIGHTS]
        self.assertEqual(set(section_indices(rows)), set(WEIGHTS))
        for invalid in (rows[:-1], [{"section_type": key} for key in WEIGHTS]):
            with self.assertRaises(ValueError):
                section_indices(invalid)

    def test_dense_is_weighted_not_global_max(self):
        groups = {key: [i] for i, key in enumerate(WEIGHTS)}
        similarities = np.zeros((2, 6))
        similarities[0, 0] = .8  # sales
        similarities[1, 5] = 1.0  # overview, larger max but smaller weighted score
        result = aggregate(dense_scores(similarities, groups), WEIGHTS)
        np.testing.assert_allclose(result, [.24, .10])

    def test_weekly_dense_selection_and_tie_breaks(self):
        def event(key, score, day, url):
            return SimpleNamespace(event_id=key, rel_dense=score,
                                   representative_article_date=day, representative_url=url)
        rows = [event('old', .9, '2025-01-07', 'a'),
                event('2', .9, '2025-01-08', 'a'),
                event('1', .9, '2025-01-08', 'a'),
                event('b', .9, '2025-01-08', 'b'),
                event('low', .1, '2025-01-12', 'a'),
                event('next', .2, '2025-01-13', 'a')]
        chosen, ranks = pipeline._weekly_dense_selection(
            rows, collect_date=date(2025, 1, 13), events_per_week=3)
        self.assertEqual([r.event_id for r in chosen], ['1', '2', 'b', 'next'])
        self.assertEqual(ranks['old'], 4)
        self.assertEqual(ranks['next'], 1)
        ranked, top = pipeline._rank_events_by_dense(chosen, 2)
        self.assertEqual([r.event_id for r in top], ['1', '2'])
        self.assertEqual(pipeline._rank_events_by_dense(chosen, None)[1], ranked)
        for limit in (0, -1):
            with self.assertRaises(ValueError):
                pipeline._weekly_dense_selection(rows, collect_date=date(2025, 1, 13), events_per_week=limit)
            with self.assertRaises(ValueError):
                pipeline._rank_events_by_dense(rows, limit)

    def test_no_cross_encoder_in_selection_module(self):
        self.assertFalse(hasattr(pipeline, 'Reranker'))
        self.assertFalse((ROOT / 'src/Agent_Team/News_Agent/ranking/rerank.py').exists())

    def test_integrated_run_copies_dense_defaults_for_both_companies(self):
        from orchestration.full_report_pipeline import _write_news_config
        with tempfile.TemporaryDirectory() as tmp:
            for company in ('대상기업', '비교기업'):
                output = Path(tmp) / company
                path = Path(tmp) / f'{company}.yaml'
                _write_news_config(path, output)
                config = yaml.safe_load(path.read_text())
                self.assertEqual(config['news']['weekly_top_k'], 3)
                self.assertIsNone(config['news']['monthly_top_k'])
                self.assertNotIn('reranker_model_name', config['models'])
                self.assertEqual(config['data_root'], str(output / 'artifacts'))

    def test_context_builder_uses_six_sections_for_any_company(self):
        class Embedder:
            _model = object()
            def __init__(self, *args, **kwargs): pass
            def encode(self, texts): return np.ones((len(texts), 6))
        for company in ("대상기업", "비교기업"):
            with tempfile.TemporaryDirectory() as tmp:
                config = {"data_root": tmp, "scoring": {"section_weighting": {"enabled": True}}}
                with patch("pathlib.Path.read_text", return_value=report_xml()), patch(
                    "Agent_Team.News_Agent.pipelines.build_corporate_context_db.EmbeddingModel", Embedder
                ):
                    build_context_db(config=config, company_id="id", company_name=company,
                                     report_key="key", report_date="20250814", report_path="fixture.xml")
                path = Path(tmp) / "db/corporate_context/id/key/corporate_context_db.jsonl"
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                self.assertEqual(set(section_indices(rows)), set(WEIGHTS))
                self.assertTrue(all(row["company_name"] == company for row in rows))

    def test_end_to_end_selection_and_legacy_storage(self):
        records = [
            RawNewsRecord(collect_date="2025-01-12", article_id=str(i), article_date="2025-01-10",
                          source="fixture", url=f"https://example.test/{i}", title=f"기업 기사{i}",
                          snippet="", doc_text=str(i), query_used="기업", lang="ko", fetched_at="2025-01-12")
            for i in range(21)
        ]
        from dataclasses import replace
        records.extend([
            replace(records[0], article_id='21', url='https://example.test/21', title='다른관계사 실적'),
            replace(records[0], article_id='22', url='https://example.test/22', title='기업홀딩스 실적'),
            replace(records[0], article_id='23', url='https://example.test/23', title='기업 미확보 기사'),
            records[0],  # Same URL is fetched once, but same titles at distinct URLs survive.
        ])
        attempted_sizes = []
        class Collector:
            def __init__(self, *args, **kwargs): pass
            def collect(self, **kwargs): return records, {}
            def _enrich_records(self, rows, notes):
                from dataclasses import replace
                from Agent_Team.News_Agent.collectors.google_news_collector import SNIPPET_POLICY
                attempted_sizes.append(len(rows))
                return [replace(row, snippet="" if row.url.endswith('/23') else "기사에서 확인되는 공급계약과 사업 변화의 짧은 발췌문이다.",
                                metadata={"snippet_policy": SNIPPET_POLICY, "snippet_source": "collector"}) for row in rows]
        class Embedder:
            _model = object()
            def __init__(self, *args, **kwargs): pass
            def encode(self, texts):
                values = np.zeros((len(texts), 6), dtype=np.float32)
                for row, text in enumerate(texts):
                    self.assert_prepared(text)
                    number = text.split('기사')[1].split(' ')[0]
                    values[row, 0 if number.isdigit() and int(number) < 15 else 5] = 1
                return values
            def assert_prepared(self, text):
                assert '[SEP]' in text and '공급계약' in text
        for enabled, monthly, weekly_limit in (
            (True, False, 3), (False, False, 3), (True, True, 3),
            (True, True, None), (True, False, 5), (True, "articles", 5),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                config = {
                    "data_root": tmp,
                    "inputs_root": str(Path(tmp) / "inputs"),
                    "news": {"collection_days": 7, "event_top_k": 3},
                    "scoring": {"section_weighting": {"enabled": enabled}},
                }
                context = [
                    {"section_type": key, "ranking_policy": POLICY_VERSION, "chunk_id": key,
                     "text": key, "embedding": np.eye(6)[i].tolist()}
                    for i, key in enumerate(WEIGHTS)
                ]
                if monthly:
                    config["news"]["monthly_top_k"] = 2
                if monthly == "articles":
                    config["news"]["raw_news_policy"] = "monthly_selected_articles_v1"
                    config["news"]["monthly_top_k"] = None
                if weekly_limit is not None:
                    config["news"]["weekly_top_k"] = weekly_limit
                expected_weekly = weekly_limit if weekly_limit is not None else 3
                xml = Path(tmp) / 'inputs/dart/id/key/기업_latest_periodic.xml'
                xml.parent.mkdir(parents=True)
                xml.write_text('<DOCUMENT><SECTION-2><TITLE>계열회사 현황(상세)</TITLE><TABLE><TR>'
                               '<TH>기업명</TH><TH>법인등록번호</TH></TR><TR>'
                               '<TD>기업홀딩스</TD><TD>110111-0000001</TD></TR><TR>'
                               '<TD>다른관계사</TD><TD>110111-0000002</TD></TR></TABLE></SECTION-2>'
                               '<SECTION-2><TITLE>연결대상 종속회사 현황(상세)</TITLE><TABLE>'
                               '<TR><TH>상호</TH></TR><TR><TD>-</TD></TR></TABLE></SECTION-2></DOCUMENT>')
                save_jsonl(context, Path(tmp) / "db/corporate_context/id/key/corporate_context_db.jsonl")
                with patch.object(pipeline, "GoogleNewsCollector", Collector), patch.object(
                    pipeline, "EmbeddingModel", Embedder
                ), patch.object(
                    pipeline, "_cluster_articles_within_week", side_effect=lambda records, *a, **k: {i: [i] for i in range(len(records))}
                ):
                    result = pipeline.run_news_window(config=config, collect_date=date(2025, 1, 12),
                                                      company_id="id", company_name="기업", report_key="key")
                report = json.loads(Path(result["report_context_path"]).read_text())
                self.assertEqual(attempted_sizes[-1], 24)
                self.assertEqual(report['news_selection']['candidate_preparation']['eligible_count'], 21)
                self.assertEqual(len(report["news_events_all"]), 21)
                self.assertEqual(len(report["news_events_weekly"]), expected_weekly)
                self.assertEqual(len(report["news_events_final"]),
                                 expected_weekly if monthly == "articles" else 2 if monthly else 3)
                self.assertFalse(report['news_selection']['reranking_enabled'])
                self.assertEqual(report['news_selection']['weekly_top_k'], expected_weekly)
                for event in report['news_events_all']:
                    self.assertEqual(event['scores']['final_score'], event['scores']['rel_dense'])
                    self.assertNotIn('rel_rerank', event['scores'])
                    self.assertNotIn('rerank_section_scores', event['scores'])
                from Agent_Team.News_Agent.collectors.report_snippets import require_prepared_summary_snippets
                require_prepared_summary_snippets(report)
                self.assertTrue(all(e["representative"]["snippet"] for e in report["news_events_weekly"]))
                self.assertEqual(sum(bool(e["representative"]["snippet"]) for e in report["news_events_all"]), 21)
                from Agent_Team.News_Agent.collectors.candidate_preparation import require_common_candidate_pool
                require_common_candidate_pool(report)
                if enabled:
                    self.assertEqual(report["news_selection"]["section_weights"], WEIGHTS)
                    for event in report["news_events_final"]:
                        self.assertLess(int(event["event_id"]), 15)
                        self.assertAlmostEqual(event["scores"]["final_score"], .30)
                        self.assertEqual(set(event["scores"]["dense_section_scores"]), set(WEIGHTS))


if __name__ == "__main__":
    unittest.main()
