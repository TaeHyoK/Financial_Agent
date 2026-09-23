"""Neutral evidence input and independent event arguments, without semantic gates."""
import copy
import unittest

from jsonschema import Draft202012Validator
from test_annual_context import strategy_fixture
from shared.evidence_cards import card_content_sha256
from Agent_Team.Strategy_Agent.decision import (
    build_strategy_context_package, strategy_decision_response_format,
    align_strategy_decision_evidence_plan, validate_strategy_decision,
)
from Agent_Team.Writer_Agent.writer_handoff import build_writer_editorial_packet
from Agent_Team.Writer_Agent.html_report_writer import _build_context, _editorial_system_prompt, normalize_report_payload
from test_optional_limits import writer_fixture
from Agent_Team.Writer_Agent.formatted_html_renderer import build_complete_html
from Agent_Team.Writer_Agent.html_report_validator import _validate_compact_text_sections


class NewsUsageHandoffTests(unittest.TestCase):
    def test_context_removes_policy_labels_not_observations_or_source_packets(self):
        packet, _, _, _ = strategy_fixture()
        original_card = next(iter(packet['cards'].values()))
        for domain in ('news', 'market', 'peer'):
            key = f'{domain}.observation'
            packet['cards'][key] = {**copy.deepcopy(original_card), 'card_key': key,
                'domain': domain, 'evidence_role': 'reference', 'machine_blockers': [{'code': 'old_gate'}],
                'primary_observation': {'event_summary': '검증기업이 신제품을 출시했다.',
                    'source_date': '2025-10-03', 'company_specificity': 'direct',
                    'event_status': 'announced', 'financial_link_status': 'not_observed',
                    'evidence_origin': 'raw_source', 'coverage': {'primary_source_present': False},
                    'event_timeline': [{'date': '2025-10-03', 'title': '출시', 'relevance_rank': 3}],
                    'final_score': .9, 'relevance_rank': 1, 'scores': {'dart': .8},
                    'ablation_selection': {'condition': 'full'}, 'value': -10}}
        before = copy.deepcopy(packet)
        context = build_strategy_context_package(packet, input_bundle={})
        self.assertEqual(set(context['evidence_cards']), set(packet['cards']))
        for card in context['evidence_cards'].values():
            self.assertNotIn('evidence_role', card)
            self.assertNotIn('machine_blockers', card)
        expected = copy.deepcopy(before['cards']['news.observation']['primary_observation'])
        for key in ('final_score', 'relevance_rank', 'scores', 'ablation_selection'):
            expected.pop(key)
        expected['event_timeline'][0].pop('relevance_rank')
        self.assertEqual(context['evidence_cards']['news.observation']['primary_observation'], expected)
        self.assertEqual(context['evidence_cards']['market.observation']['primary_observation'],
                         before['cards']['market.observation']['primary_observation'])
        self.assertEqual(packet, before)

    def test_independent_events_of_same_type_survive_schema_alignment_and_writer(self):
        packet, context, decision, provenance = strategy_fixture()
        original = next(iter(packet['cards'].values()))
        for index in range(5):
            key = f'news.event_{index}'
            card = {**copy.deepcopy(original), 'card_key': key, 'domain': 'news', 'card_type': 'event',
                    'primary_observation': {'event_summary': f'독립 사건 {index}이다.',
                                            'event_date': '2025-10-03'}}
            packet['cards'][key] = card
            context['evidence_cards'][key] = card
            provenance['cards'][key] = {'strategy_card_sha256': card_content_sha256(card),
                'source_evidence_ids': [f'RAW_{index}'], 'source_paths': [], 'source_files': []}
            decision['report_insights'].append({'insight_type': 'events_and_execution',
                'text': f'독립 사건 {index}의 사업상 의미다.', 'card_keys': [key]})
        before = copy.deepcopy(decision)
        schema = strategy_decision_response_format(context)['json_schema']['schema']
        Draft202012Validator(schema).validate(decision)
        aligned = align_strategy_decision_evidence_plan(decision, context=context)
        validate_strategy_decision(aligned, context=context)
        handoff, _ = build_writer_editorial_packet(strategy_packet=packet, strategy_decision=aligned,
                                                   strategy_provenance=provenance)
        self.assertEqual(handoff['report_insights'], decision['report_insights'])
        for item in decision['report_insights']:
            self.assertTrue(set(item['card_keys']) <= set(handoff['cards']))
            self.assertTrue(set(item['card_keys']) <= set(handoff['available_card_keys_by_component']['catalysts_execution']))
        self.assertEqual(decision, before)

    def test_counterview_only_evidence_reaches_writer_without_forcing_all_cards(self):
        packet, context, decision, provenance = strategy_fixture()
        key = 'news.later_earnings'
        card = {**copy.deepcopy(next(iter(packet['cards'].values()))), 'card_key': key,
                'domain': 'news', 'card_type': 'event', 'decision_use': 'context_only',
                'primary_observation': {'event_date': '2025-10-03',
                    'event_summary': '검증기업의 신규 사업 매출 증가가 보도됐다.',
                    'company_specificity': 'direct', 'event_status': 'announced'}}
        packet['cards'][key] = card
        context['evidence_cards'][key] = card
        provenance['cards'][key] = {'strategy_card_sha256': card_content_sha256(card),
                'source_evidence_ids': ['RAW_LATER'], 'source_paths': [], 'source_files': []}
        counterview = {'text': '후행 보도의 매출 증가는 성장 지속 해석을 보강한다.', 'card_keys': [key]}
        decision['strategy_brief']['counterview'] = counterview
        aligned = align_strategy_decision_evidence_plan(decision, context=context)
        handoff, _ = build_writer_editorial_packet(strategy_packet=packet, strategy_decision=aligned,
                                                   strategy_provenance=provenance)
        request = _build_context(writer_handoff=handoff)
        self.assertEqual(request['writer_input']['recommendation_bridge']['counterview'], counterview['text'])
        self.assertEqual(request['writer_input']['recommendation_bridge']['counterview_card_keys'], [key])
        self.assertIn(key, handoff['available_card_keys_by_component']['investment_call_thesis'])
        self.assertNotIn(key, handoff['required_card_keys_by_component']['key_evidence_table'])
        self.assertNotIn('decision_use', handoff['cards'][key])
        self.assertEqual(handoff['cards'][key]['source_metadata']['event_status'], 'announced')
        self.assertIn('counterview', request['writing_rules']['thesis_policy'])
        self.assertIn('counterview', _editorial_system_prompt())
        self.assertNotIn('두 문단 안팎', _editorial_system_prompt())

    def test_writer_keeps_distinct_event_paragraphs_without_count_or_character_cut(self):
        handoff, raw = writer_fixture()
        item = raw['sections']['catalysts_execution']['section_analysis']
        key = next(iter(handoff['cards']))
        texts = [f'서로 다른 사건 {index}의 가정과 영향이다.' + '사업상 의미를 설명한다.' * 100 for index in range(5)]
        item.update(paragraphs=texts, card_keys=[key],
                    _claim_units=[{'claim': text, 'card_keys': [key], 'limitation_categories': []} for text in texts])
        before = copy.deepcopy(raw)
        report = normalize_report_payload(raw, writer_handoff=handoff)
        html = build_complete_html(report)
        for text in texts:
            self.assertIn(text, html)
        self.assertEqual(raw, before)
        self.assertEqual(_validate_compact_text_sections(report, handoff, []), 'pass')


if __name__ == '__main__':
    unittest.main()
