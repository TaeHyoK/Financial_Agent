"""Raw News primary input, monthly summary subdata and selection provenance."""
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from shared.news_articles import ARTICLE_NEWS_POLICY, build_article_packet, article_catalog, articles_for_llm
from shared.subdata import news_subdata, secondary_context_for_llm
from orchestration.dependency_graph import STEP_SPECS
from Agent_Team.News_Agent import context_export, analysis_agent


def report_fixture():
    rows = [{'event_id': str(i), 'mention_count': 1,
             'representative': {'time': (date(2025, 1, 6) + timedelta(weeks=i//3)).isoformat(),
                                'title': f'기업 사건 {i}', 'snippet': f'기업의 계약 {i} 원문 발췌문',
                                'source': '언론사'},
             'scores': {'final_score': .5}, 'relevance_rank': i + 1}
            for i in range(60)]
    return {'collect_date': '2025-10-30', 'company': {'company_name': '검증기업'},
            'news_selection': {'raw_news_policy': ARTICLE_NEWS_POLICY},
            'news_events_weekly': rows, 'news_events_all': rows, 'news_events_final': rows}


def test_selected_articles_are_not_reduced_to_24_or_two_per_month():
    packet = build_article_packet(report_fixture())
    assert len(packet['events']) == 60
    assert len(packet['periods']) == 12
    assert max(len(p['events']) for p in packet['periods']) > 2
    assert len(article_catalog(packet)) == 60
    text = json.dumps(packet)
    assert 'scores' not in text and 'relevance_rank' not in text


def test_empty_selection_stays_empty_and_bad_sources_fail():
    report = report_fixture()
    report['news_events_weekly'] = []
    assert build_article_packet(report)['events'] == []
    import pytest
    for bad in ('duplicate', 'date', 'snippet'):
        report = report_fixture()
        if bad == 'duplicate':
            report['news_events_weekly'].append(deepcopy(report['news_events_weekly'][0]))
        else:
            report['news_events_weekly'][0]['representative']['time' if bad == 'date' else 'snippet'] = ''
        with pytest.raises(ValueError):
            build_article_packet(report)


def test_export_and_news_request_ignore_old_summaries_and_share_exact_articles():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / 'report.json'
        source.write_text(json.dumps(report_fixture(), ensure_ascii=False))
        folder = root / 'context_exports/month'
        folder.mkdir(parents=True)
        # Deliberately unreadable old summary: even its presence must not matter.
        (folder / 'llm_period_summaries.json').write_text('BROKEN_OLD_SUMMARY')
        with patch.object(context_export, '_build_openai_client', side_effect=AssertionError('paid call')):
            exports = context_export.build_context_exports(report_context_path=source, output_dir=folder)
        assert {'news_articles_path', 'llm_summary_request_path', 'llm_period_summaries_path'} <= set(exports)
        packet = json.loads(Path(exports['news_articles_path']).read_text())
        summary_request = json.loads(Path(exports['llm_summary_request_path']).read_text())
        summary_input = context_export._load_llm_user_payload(summary_request)
        assert [e for p in summary_input['periods'] for e in p['events']] == packet['events']
        (root / 'dart.json').write_text('{}')
        (root / 'market.json').write_text('{}')
        paths = analysis_agent._resolve_paths(project_root=root, context_export_dir=folder.parent,
            granularity='month', as_of_date=date(2025, 10, 31), dart_lightweight_path=str(root/'dart.json'),
            market_summary_path=str(root/'market.json'), output_dir=str(root/'output'))
        payload = analysis_agent.build_analysis_input_payload(company_name='검증기업', ticker=None, corp_code=None,
            as_of_date=date(2025, 10, 31), paths=paths, max_raw_events_per_period=1)
        request = analysis_agent.build_llm_request(input_payload=payload, model='gpt-5.4-mini')
        body = json.loads(request['input'][1]['content'])
        groups = body['input_payload']['월별 개별 뉴스']
        actual = {key: value for p in groups for key, value in p['articles'].items()}
        secondary = secondary_context_for_llm({'news': news_subdata(packet)})['news']['evidence_catalog']
        assert actual == secondary == articles_for_llm(article_catalog(packet))
        assert len(actual) == 60
        assert 'NEWS_PERIOD' not in json.dumps(request)
        assert not body['input_payload'].get('최근 1년 월별 요약 12개')
        assert all(row['origin_type'] == 'raw_source' for row in payload['evidence_map'].values())
        with patch.object(context_export, '_run_llm_summary') as run:
            context_export.execute_llm_summary_request(llm_request_path=folder/'llm_summary_request.json')
        assert run.call_count == 1


def test_execution_graph_separates_raw_news_from_summary_subdata():
    assert 'news_llm' in {step.name for step in STEP_SPECS}
    for step in STEP_SPECS:
        if step.name == 'news_analysis':
            assert 'news_export' in step.dependencies
            assert 'news_llm' not in step.dependencies
        elif step.name in {'financial_analyst', 'yfinance_report'}:
            assert 'news_llm' in step.dependencies


def test_runtime_subdata_routes_to_summary_and_primary_only_omits_it():
    from orchestration.end_to_end_loop import AgentTeamOrchestrator, build_parser
    from test_annual_review import source_snapshot
    with tempfile.TemporaryDirectory() as tmp:
        config, source, _ = source_snapshot(Path(tmp))
        for primary_only in (False, True):
            argv = ['--config', str(config.config_path), '--output-root', str(source.output_root)]
            if primary_only:
                argv += ['--primary-data-only']
            runner = AgentTeamOrchestrator(build_parser().parse_args(argv))
            manifest = json.loads(runner.paths.financial_runtime_manifest.read_text())
            assert manifest['input_paths']['news_weekly_summaries'] == ('' if primary_only else str(runner.paths.news_llm_period_summaries))
            command = runner._yfinance_report_command()
            assert command[command.index('--news-json') + 1] == str(runner.paths.news_llm_period_summaries)


def test_summary_generation_preserves_articles_and_request_identity():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / 'report.json'
        source.write_text(json.dumps(report_fixture()))
        def summarize(client, request):
            periods = context_export._load_llm_user_payload(request)['periods']
            output = {'periods': [
                {'period': p['period'], 'issues': [
                    {'summary': e['snippet'], 'source_event_ids': [e['event_id']]}
                    for e in p['events']]} for p in periods]}
            context_export._attach_source_event_ids(output, request)
            return {'usage': {}, 'output': output}
        with patch.object(context_export, '_build_openai_client', return_value=object()), \
                patch.object(context_export, '_call_llm_summary', side_effect=summarize) as call:
            exports = context_export.build_context_exports(report_context_path=source, output_dir=root/'export', run_llm=True)
        summary = json.loads(Path(exports['llm_period_summaries_path']).read_text())
        request = json.loads(Path(exports['llm_summary_request_path']).read_text())
        assert call.call_count == 1
        assert summary['source_request_sha256'] == context_export.summary_request_hash(request)
        assert news_subdata(summary)['input_type'] == 'monthly_news_summaries'
        assert len(news_subdata(summary)['evidence_catalog']) == 60
        assert len(json.loads(Path(exports['news_articles_path']).read_text())['events']) == 60


def linked_summary_fixture():
    periods = [{'period': '2025-01', 'period_start': '2025-01-01', 'period_end': '2025-01-31',
                'events': [{'event_id': str(i), 'title': f'사건 {i}', 'snippet': f'기사 {i} 내용',
                            'time': '2025-01-15'} for i in range(30)]},
               {'period': '2025-02', 'events': [{'event_id': 'other', 'snippet': '다른 달 기사'}]},
               {'period': '2025-03', 'events': []}]
    request = context_export._build_llm_summary_request({'periods': periods}, 'gpt-5.4-mini')
    output = {'periods': [
        {'period': '2025-01', 'issues': [
            {'summary': '1월 15일 기업 A는 2024년 연간 매출을 발표했다.', 'source_event_ids': ['0']},
            {'summary': '같은 기사에서 기업 A의 별도 신규 계약도 발표했다.', 'source_event_ids': ['0']},
            {'summary': '기업 A의 생산시설 확장에 관한 진행 내역이다.', 'source_event_ids': [str(i) for i in range(1, 28)]}]},
        {'period': '2025-02', 'issues': []},
        {'period': '2025-03', 'issues': []}]}
    return request, output


def test_summary_prompt_requires_issue_sources_without_count_or_length_targets():
    request, _ = linked_summary_fixture()
    user = context_export._load_llm_user_payload(request)
    text = json.dumps(user, ensure_ascii=False)
    assert 'mention_count' not in text and 'period_summary' not in text
    assert '400자' not in text and '3개 안팎' not in text
    assert set(user['expected_output_schema']['periods'][0]['issues'][0]) == {'summary', 'source_event_ids'}


def test_summary_preserves_distinct_issues_and_all_actual_citations_in_subdata():
    request, output = linked_summary_fixture()
    original_issues = deepcopy(output['periods'][0]['issues'])
    context_export._attach_source_event_ids(output, request)
    period = output['periods'][0]
    assert period['issues'] == original_issues  # no merging because an ID is shared
    assert period['source_event_ids'] == [str(i) for i in range(28)]
    assert set(period['input_event_ids']) == {str(i) for i in range(30)}
    assert output['periods'][1]['source_event_ids'] == []  # no forced inclusion
    assert output['periods'][1]['input_event_ids'] == ['other']
    assert output['periods'][2]['issues'] == []  # no Python-written prose
    context = news_subdata({'output': output})
    delivered = secondary_context_for_llm({'news': context})['news']
    rows = list(delivered['evidence_catalog'].values())
    assert [r['text'] for r in rows] == [i['summary'] for i in original_issues]
    assert [r['source_event_ids'] for r in rows] == [i['source_event_ids'] for i in original_issues]
    assert all(r['origin_type'] == 'model_summarized' for r in rows)
    assert context['period_count'] == 3
    from Agent_Team.Financial_Agent.financial_analysis_agent import build_financial_request
    from Agent_Team.YFinance_Agent.reporting import build_market_request
    financial = {'target_company': '검증기업', 'secondary_context': {'news': context},
                 'strategy_handoff': {'key_evidence': [{'evidence_id': 'E001', 'value': 10}]}}
    market = {'company_name': '검증기업', 'market_summary': {'latest_snapshot': {'date': '2025-03-31'}},
              'primary_evidence_catalog': {'YF_1': {'evidence_id': 'YF_1', 'value': 20}},
              'secondary_context': {'news': context}}
    for req in (build_financial_request(financial, model='gpt-5.4-mini'),
                build_market_request(market, model='gpt-5.4-mini')):
        body = json.loads(req['input'][1]['content'])
        assert body['secondary_context']['news'] == delivered
        assert 'input_event_ids' not in json.dumps(body)
    context_export._attach_source_event_ids(output, request)  # snapshot validation is idempotent
    assert period['issues'] == original_issues


def test_invalid_summary_ids_and_structure_fail_without_repair():
    import pytest
    for bad in ([], ['unknown'], ['other'], [0], '0'):
        request, output = linked_summary_fixture()
        output['periods'][0]['issues'][0]['source_event_ids'] = bad
        with pytest.raises(ValueError, match='source IDs'):
            context_export._attach_source_event_ids(output, request)
        assert output['periods'][0]['issues'][0]['source_event_ids'] == bad
    request, output = linked_summary_fixture()
    output['periods'].pop()
    with pytest.raises(ValueError, match='every requested period'):
        context_export._attach_source_event_ids(output, request)
    with pytest.raises(ValueError, match='periods list'):
        context_export._attach_source_event_ids({'raw_content': 'broken'}, request)


def test_split_and_single_summary_share_the_same_issue_contract():
    request, output = linked_summary_fixture()
    context_export._attach_source_event_ids(output, request)
    results = []
    for period, part in context_export._build_period_llm_requests(request):
        item = deepcopy(next(p for p in output['periods'] if p['period'] == period))
        response = {'periods': [item]}
        context_export._attach_source_event_ids(response, part)
        results.append({'period': period, 'status': 'success', 'output': item, 'usage': {}})
    combined = context_export._split_summary_payload(request, results)
    assert combined['output'] == output
    assert combined['source_request_sha256'] == context_export.summary_request_hash(request)


def test_reuse_rejects_summary_from_other_selection_before_calls():
    import pytest
    from test_annual_review import complete_domain_snapshot
    from orchestration.end_to_end_loop import materialize_reused_domain_snapshot
    with tempfile.TemporaryDirectory() as tmp:
        config, source, destination = complete_domain_snapshot(Path(tmp))
        report = json.loads(source.news_report_context.read_text())
        for key in ('news_events_all', 'news_events_weekly', 'news_events_final'):
            report[key][0]['representative']['snippet'] = '변경된 기사 발췌문'
        source.news_report_context.write_text(json.dumps(report))
        source.news_articles.write_text(json.dumps(build_article_packet(report)))
        with patch.object(context_export, '_build_openai_client', side_effect=AssertionError('paid call')):
            with pytest.raises(ValueError, match='summaries do not match'):
                materialize_reused_domain_snapshot(run_config=config, source_root=source.output_root,
                    destination_paths=destination, expected_news_model='offline')


def test_expected_calls_include_summary_only_when_generated():
    from types import SimpleNamespace
    from orchestration.full_report_pipeline import _expected_calls
    config = SimpleNamespace(primary_data_only=False, include_competitor=True)
    assert _expected_calls(config) == {'target': 15, 'peer': 15, 'final': 3}
    assert _expected_calls(config, reused_domain_snapshot=True) == {'target': 3, 'peer': 3, 'final': 3}
    config.primary_data_only = True
    assert _expected_calls(config) == {'target': 3, 'peer': 3, 'final': 3}
    config.include_competitor = False
    assert _expected_calls(config) == {'target': 3, 'peer': 0, 'final': 2}
