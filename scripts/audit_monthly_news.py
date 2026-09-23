"""Offline monthly allocation audit over saved ranked events; never calls an LLM."""
import argparse
import copy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from shared.news_selection import select_monthly_news, MONTHLY_NEWS_POLICY, event_period
from shared.time_windows import monthly_windows
from ablation_suite.annual_random import select_annual_random_events


def run(args):
    output=Path(args.output_dir).resolve(); output.mkdir(parents=True,exist_ok=False)
    audit=[]
    for path in map(Path,args.report_context):
        source_hash=hashlib.sha256(path.read_bytes()).hexdigest()
        report=json.loads(path.read_text()); end=date.fromisoformat(report['collect_date'])+timedelta(days=1)
        windows=monthly_windows(end)
        def stamp(row):return str(row.get('representative',{}).get('time') or '')[:10]
        # Preserve the recorded selection policy when auditing historical files.
        selection = report.get('news_selection', {})
        if selection.get('reranking_enabled') is False:
            score_key = 'rel_dense'
        elif selection.get('metric') == 'section_weighted_similarity':
            score_key = 'final_score'
        else:
            score_key = 'rel_rerank'
        if any(score_key not in row.get('scores',{}) for row in report['news_events_weekly']):
            raise ValueError(f'Missing recorded ranking score {score_key}; do not replace scores with zero')
        ranked=sorted(report['news_events_weekly'],key=lambda row:(
            -float(row['scores'][score_key]),
            -float(row.get('scores',{}).get('rel_dense',0)),
            -date.fromisoformat(stamp(row)).toordinal(),str(row.get('representative',{}).get('url') or ''),str(row['event_id'])))
        selected=select_monthly_news(ranked,end_exclusive=end,time_of=stamp,id_of=lambda row:row['event_id'])
        revised=copy.deepcopy(report)
        revised.setdefault('news_selection',{}).update(raw_news_policy=MONTHLY_NEWS_POLICY,monthly_top_k=2,
            selected_event_count=len(selected),top_k=None,offline_reselection=True)
        revised['news_events_final']=selected
        revised['news_events_topk']=copy.deepcopy(selected)
        random_report,random_audit=select_annual_random_events(revised,seed=args.seed,sample_size=24)
        company=report['company']['company_name']; dest=output/company;dest.mkdir(exist_ok=False)
        for name,value in [('monthly_report_context',revised),('random_report_context',random_report),('random_audit',random_audit)]:
            (dest/f'{name}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
        old_ids={row['event_id'] for row in report['news_events_final']}
        monthly=[]
        for w in windows:
            items=[r for r in selected if event_period(stamp(r),windows)==w['period']]
            monthly.append({'period':w['period'],
                'common_pool':sum(event_period(stamp(r),windows)==w['period'] for r in report['news_events_all']),
                'weekly_selected':sum(event_period(stamp(r),windows)==w['period'] for r in report['news_events_weekly']),
                'old_final_count':sum(event_period(stamp(r),windows)==w['period'] for r in report['news_events_final']),
                'monthly_final_count':len(items),'random_final_count':random_audit['monthly_final_counts'][w['period']],
                'articles':[{'date':stamp(r),'title':r['representative']['title'],'previously_selected':r['event_id'] in old_ids} for r in items]})
        audit.append({'company':company,'source':str(path.resolve()),'source_sha256':source_hash,
            'source_unchanged':hashlib.sha256(path.read_bytes()).hexdigest()==source_hash,
            'old_count':len(old_ids),'new_count':len(selected),'overlap':len(old_ids & {r['event_id'] for r in selected}),
            'monthly':monthly})
    (output/'audit.json').write_text(json.dumps({'api_calls':0,'scope':'offline reselection only; no regenerated summaries or domain reports','companies':audit},ensure_ascii=False,indent=2)+'\n')
    lines=['# 월별 뉴스 선정 오프라인 비교','','API 호출 없음. 기존 주별 순위·후보를 재사용했고, 원본 파일은 수정하지 않았다. 이 결과는 완성된 파이프라인 원본이나 새 모델 실험 결과가 아니다.','']
    for item in audit:
        lines.extend([f"## {item['company']}",'',f"기존 {item['old_count']}건 → 월별 선정 {item['new_count']}건, 공통 {item['overlap']}건.",'',
            '| 기간 | 공통 후보 | 주별 선정 | 기존 최종 | 새 최종 | random |','| --- | ---: | ---: | ---: | ---: | ---: |'])
        for m in item['monthly']:lines.append(f"| {m['period']} | {m['common_pool']} | {m['weekly_selected']} | {m['old_final_count']} | {m['monthly_final_count']} | {m['random_final_count']} |")
        lines.extend(['','### 선정 기사',''])
        for m in item['monthly']:
            for article in m['articles']:lines.append(f"- {article['date']} {article['title']} ({'기존 포함' if article['previously_selected'] else '새로 포함'})")
        lines.append('')
    (output/'audit.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps([{k:v for k,v in row.items() if k!='monthly'} for row in audit],ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--report-context',action='append',required=True)
    p.add_argument('--output-dir',required=True);p.add_argument('--seed',type=int,default=20251031)
    run(p.parse_args())
