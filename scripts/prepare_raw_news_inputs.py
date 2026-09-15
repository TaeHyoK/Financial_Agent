"""Historical all-raw-subdata experiment audit, NOT the production input policy.

Production uses monthly summaries for financial/market subdata. This isolated
comparison retains raw subdata to reproduce the 2026-09-14 input-size experiment.
No API clients or report generation.
"""
import argparse
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'src/Agent_Team/YFinance_Agent'), '/home/agent2/ABLATION']
from shared.news_articles import ARTICLE_NEWS_POLICY, article_catalog, articles_for_llm
from shared.subdata import news_subdata, secondary_context_for_llm
from shared.llm_clients import measure_request
from Agent_Team.News_Agent import context_export, analysis_agent
from Agent_Team.News_Agent.pipelines.run_news_pipeline import _weekly_dense_selection
from Agent_Team.Financial_Agent.langgraph_flow import build_financial_analyst_output
from Agent_Team.Financial_Agent.financial_analysis_agent import build_financial_request
from Agent_Team.YFinance_Agent import reporting
from ablation_suite.annual_random import select_annual_random_events


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def run(args):
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    hashes, measurements = {}, []
    def read(path):
        path = Path(path).resolve()
        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return json.loads(path.read_text())

    # Prevent accidental collection or model calls even if a builder changes.
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('offline audit forbids network')):
        for role in ('target', 'peer'):
            source = read(getattr(args, role + '_report'))
            domain_root = Path(getattr(args, role + '_domains')).resolve()
            collect_date = date.fromisoformat(source['collect_date'])
            selected_date = (collect_date.toordinal() + 1)
            boundary = date.fromordinal(selected_date)
            day = boundary.strftime('%Y%m%d')
            company = source['company']['company_name']
            candidates = [SimpleNamespace(event_id=str(r['event_id']), rel_dense=r['scores']['rel_dense'],
                representative_article_date=r['representative']['time'][:10],
                representative_url=r['representative'].get('url')) for r in source['news_events_all']]
            chosen, _ = _weekly_dense_selection(candidates, collect_date=collect_date, events_per_week=3)
            by_id = {str(r['event_id']): r for r in source['news_events_all']}
            full = deepcopy(source)
            full['news_events_weekly'] = [deepcopy(by_id[r.event_id]) for r in chosen]
            full['news_events_final'] = deepcopy(full['news_events_weekly'])
            full['news_selection'].update(raw_news_policy=ARTICLE_NEWS_POLICY, monthly_top_k=None,
                top_k=None, weekly_top_k=3, reranking_enabled=False, stage='section_weighted_dense_v2')
            random_report, audit = select_annual_random_events(full, seed=args.seed)
            save(output / role / 'random_selection_audit.json', audit)
            financial_dir = domain_root / 'Financial' / day
            market_dir = domain_root / 'Y_Finance' / day
            dart_main, dart_master = read(financial_dir/'dart_main.json'), read(financial_dir/'dart_master.json')
            market_summary = read(market_dir/'market_summary.json')
            previous = read(domain_root / 'News' / day / 'output/news_agent_input_payload.json')
            # Capture the actual market builder immediately before generation.
            class Prepared(Exception):
                pass
            market_payload = {}
            def capture(payload, **kwargs):
                market_payload.update(deepcopy(payload))
                raise Prepared()
            for name in ('market_full_dataset.json', 'manifest.json', 'valuation_snapshot.json'):
                read(market_dir/name)
            read(financial_dir/'dart_lightweight.json')
            with patch.object(reporting, 'generate_agent_json_report_with_llm', side_effect=capture):
                try:
                    reporting.generate_analyst_report(market_json=market_dir/'market_full_dataset.json',
                        dart_json=financial_dir/'dart_lightweight.json', news_json=Path('unused'),
                        valuation_json=market_dir/'valuation_snapshot.json', report_md=output/'unused.md',
                        report_json=output/'unused.json', company_name=company, primary_data_only=True)
                except Prepared:
                    pass
            assert market_payload, 'Market request capture failed'
            previous_summaries = read(domain_root / 'News' / day / 'context_exports/month/llm_period_summaries.json')
            counts = {}
            for condition, report in (('full', full), ('random', random_report)):
                dest = output / role / condition
                save(dest/'report_context.json', report)
                exports = context_export.build_context_exports(report_context_path=dest/'report_context.json',
                                                               output_dir=dest/'context_exports/month')
                packet = json.loads(Path(exports['news_articles_path']).read_text())
                counts[condition] = [len(p['events']) for p in packet['periods']]
                context = news_subdata(packet)
                paths = analysis_agent._resolve_paths(project_root=ROOT, context_export_dir=dest/'context_exports',
                    granularity='month', as_of_date=boundary, dart_lightweight_path=str(financial_dir/'dart_lightweight.json'),
                    market_summary_path=str(market_dir/'market_summary.json'), output_dir=str(dest/'news'))
                news = analysis_agent.build_analysis_input_payload(company_name=company,
                    ticker=previous['target_entity'].get('ticker'), corp_code=previous['target_entity'].get('corp_code'),
                    as_of_date=boundary, paths=paths, max_raw_events_per_period=1)
                inputs = {'dart_main': dart_main, 'dart_master': dart_master,
                          'yfinance_market_summary': market_summary, 'news_weekly_summaries': packet}
                financial = build_financial_analyst_output({'target_entity': previous['target_entity']}, inputs)
                market = deepcopy(market_payload)
                market['secondary_context'] = {'financial': news['secondary_context']['financial'], 'news': context}
                assert financial['secondary_context']['news'] == context
                requests = {'news': analysis_agent.build_llm_request(input_payload=news, model=args.model),
                            'financial': build_financial_request(financial, model=args.model),
                            'market': reporting.build_market_request(market, ticker=previous['target_entity'].get('ticker'), model=args.model)}
                before_tokens = {}
                if condition == 'full':
                    old_financial = deepcopy(financial)
                    old_financial['secondary_context']['news'] = news_subdata(previous_summaries)
                    old_market = deepcopy(market)
                    old_market['secondary_context']['news'] = news_subdata(previous_summaries)
                    old_requests = {
                        'financial': build_financial_request(old_financial, model=args.model),
                        'market': reporting.build_market_request(old_market, ticker=previous['target_entity'].get('ticker'), model=args.model),
                        'news': read(domain_root / 'News' / day / 'output/news_agent_llm_request.json')}
                    before_tokens = {key: measure_request(req, model=args.model).estimated_input_tokens
                                     for key, req in old_requests.items()}
                raw_catalog = articles_for_llm(article_catalog(packet))
                body = json.loads(requests['news']['input'][1]['content'])['input_payload']
                assert {k: v for p in body['월별 개별 뉴스'] for k, v in p['articles'].items()} == raw_catalog
                for domain in ('financial', 'market'):
                    delivered = json.loads(requests[domain]['input'][1]['content'])['secondary_context']['news']['evidence_catalog']
                    assert delivered == raw_catalog, f'{domain} dropped or changed article content'
                save(dest/'news/news_agent_input_payload.json', news)
                save(dest/'news/news_agent_evidence_map.json', news['evidence_map'])
                for domain, request in requests.items():
                    save(dest/f'{domain}_request.json', request)
                    measured = measure_request(request, model=args.model).estimated_input_tokens
                    measurements.append({'role': role, 'company': company, 'condition': condition, 'domain': domain,
                        'articles': len(packet['events']), 'monthly_counts': counts[condition],
                        'input_tokens': measured, 'input_tokens_with_saved_summary': before_tokens.get(domain), 'output_limit': request['max_output_tokens']})
            assert counts['full'] == counts['random']
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in hashes.items())
    costs = {}
    for condition in ('full', 'random'):
        rows = [r for r in measurements if r['condition'] == condition]
        tokens = sum(r['input_tokens'] for r in rows)
        costs[condition] = {model: {'six_domain_input_usd': round(tokens * inp / 1e6, 4),
            'six_domain_4k_to_12k_output_each_usd': [round((tokens*inp + 6*n*out)/1e6, 4) for n in (4000, 12000)]}
            for model, inp, out in [('gpt-5.4-mini', .75, 4.5), ('gpt-5.4', 2.5, 15)]}
    save(output/'audit.json', {'status': 'prepared_offline', 'paid_calls': 0, 'model': args.model,
        'seed': args.seed, 'source_hashes': hashes, 'sources_unchanged': True, 'rows': measurements, 'costs': costs,
        'scope': 'Experimental all-raw subdata, not current production (which uses monthly summary subdata). Six domain requests per condition.',
        'cost_assumptions': 'Standard API, no cache discount or retries. Output is a scenario, not a forecast. Downstream three calls excluded.',
        'pricing_source': 'https://developers.openai.com/api/docs/pricing'})
    print(json.dumps({'rows': measurements, 'costs': costs}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for role in ('target', 'peer'):
        parser.add_argument(f'--{role}-report', required=True)
        parser.add_argument(f'--{role}-domains', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--seed', type=int, default=20251107)
    parser.add_argument('--model', default='gpt-5.4-mini')
    run(parser.parse_args())
