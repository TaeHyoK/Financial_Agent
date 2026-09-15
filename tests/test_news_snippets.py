import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from Agent_Team.News_Agent import context_export
from Agent_Team.News_Agent.collectors.google_news_collector import (
    GoogleNewsCollector, SNIPPET_POLICY, _extract_article_snippet, _truncate_snippet,
)
from Agent_Team.News_Agent.collectors.report_snippets import enrich_report_snippets, require_prepared_summary_snippets


def event(key, snippet=""):
    return {"event_id": key, "member_article_ids": [key], "mention_count": 1,
            "scores": {"final_score": .7}, "representative": {
                "title": f"검증기업 {key} 공급계약 체결", "snippet": snippet,
                "time": "2025-10-15", "url": f"https://example.com/{key}"}}


def report(selected):
    pool = [event(k) for k in ("a", "b", "c")]
    by_id = {e["event_id"]: e for e in pool}
    return {"collect_date": "2025-10-30", "company": {"company_name": "검증기업"},
            "news_events_all": pool, "news_events_weekly": [copy.deepcopy(by_id[k]) for k in selected],
            "news_events_final": [copy.deepcopy(by_id[selected[0]])]}


class NewsSnippetTests(unittest.TestCase):
    def collector(self):
        collector = GoogleNewsCollector()
        collector._fetch_publisher_snippet = Mock(side_effect=lambda url, title, **kwargs:
            "" if url.endswith('/c') else f"{title}. 계약 기간은 3년이며 공급 범위 확대를 발표했다.")
        return collector

    def test_union_reuses_identical_excerpt_and_does_not_change_selection(self):
        full, random = report(["a", "b"]), report(["b", "c"])
        originals = copy.deepcopy([full, random])
        collector = self.collector()
        audit = enrich_report_snippets([full, random], collector=collector)
        self.assertEqual(audit["unique_articles"], 3)
        self.assertEqual(collector._fetch_publisher_snippet.call_count, 3)
        for modified, original in zip([full, random], originals):
            require_prepared_summary_snippets(modified)
            for view in ("news_events_all", "news_events_weekly", "news_events_final"):
                for after, before in zip(modified[view], original[view]):
                    self.assertEqual({k:v for k,v in after.items() if k != 'representative'},
                                     {k:v for k,v in before.items() if k != 'representative'})
                    for key in ("title", "time"):
                        self.assertEqual(after['representative'][key], before['representative'][key])
        full_by_id = {e['event_id']:e['representative'] for e in full['news_events_all']}
        self.assertTrue(all(e['representative'] == full_by_id[e['event_id']] for e in random['news_events_weekly']))
        enrich_report_snippets([full, random], collector=collector)
        self.assertEqual(collector._fetch_publisher_snippet.call_count, 3, "Missing snippets must also stay frozen")

    def test_random_expands_frozen_full_without_retrying_shared_article(self):
        full = report(["a", "b"])
        collector = self.collector()
        enrich_report_snippets([full], collector=collector)
        random = copy.deepcopy(full)
        pool = {e['event_id']:e for e in random['news_events_all']}
        random['news_events_weekly'] = [copy.deepcopy(pool['b']), copy.deepcopy(pool['c'])]
        random['news_events_final'] = [copy.deepcopy(pool['c'])]
        frozen_full = copy.deepcopy(full)
        enrich_report_snippets([random], collector=collector)
        self.assertEqual(collector._fetch_publisher_snippet.call_count, 3)
        self.assertEqual(full, frozen_full)
        self.assertEqual(random['news_events_weekly'][0]['representative'], full['news_events_weekly'][1]['representative'])
        self.assertEqual(random['news_events_final'][0]['representative']['snippet'], '')

    def test_existing_excerpt_is_kept_short_without_network_fetch(self):
        full = report(["a"])
        for view in ("news_events_all", "news_events_weekly", "news_events_final"):
            full[view][0]['representative']['snippet'] = "검증기업은 신규 공급계약을 체결했다고 공시했다. " * 30
        collector = self.collector()
        enrich_report_snippets([full], collector=collector)
        self.assertFalse(collector._fetch_publisher_snippet.called)
        self.assertLessEqual(len(full['news_events_final'][0]['representative']['snippet']), 283)

    def test_summary_and_raw_exports_receive_excerpts(self):
        full = report(["a", "b"])
        enrich_report_snippets([full], collector=self.collector())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'report.json'
            path.write_text(json.dumps(full))
            result = context_export.build_context_exports(report_context_path=path, output_dir=Path(tmp)/'exports')
            summary = json.loads(Path(result['summary_prompt_input_path']).read_text())
            raw = json.loads(Path(result['recent_raw_input_path']).read_text())
        self.assertTrue(all(e['snippet'] for p in summary['periods'] for e in p['events']))
        self.assertEqual(len([e for p in summary['periods'] for e in p['events']]), 2)
        self.assertTrue(raw['events'][0]['snippet'])
        require_prepared_summary_snippets(full, summary_input=summary)
        for period in summary['periods']:
            for event in period['events']:
                event['snippet'] = ''
        with self.assertRaisesRegex(ValueError, 'Regenerate Full summaries'):
            require_prepared_summary_snippets(full, summary_input=summary)

    def test_unprepared_source_and_conflicting_frozen_values_are_errors(self):
        full, other = report(["a"]), report(["a"])
        with self.assertRaisesRegex(ValueError, 'Regenerate Full'):
            require_prepared_summary_snippets(full)
        collector = self.collector()
        enrich_report_snippets([full, other], collector=collector)
        other['news_events_weekly'][0]['representative']['snippet'] = '다른 발췌문'
        with self.assertRaisesRegex(ValueError, 'Conflicting frozen'):
            enrich_report_snippets([full, other], collector=collector)

    def test_html_encoding_is_read_from_bytes(self):
        html = '<html><meta charset="utf-8"><article itemprop="articleBody"><p>검증기업은 신규 공급계약을 체결했다고 공시했다.</p></article></html>'
        response = requests.Response()
        response.status_code = 200
        response.url = 'https://example.com/a'
        response._content = html.encode('utf-8')
        response.encoding = 'ISO-8859-1'
        self.assertNotIn('검증기업', response.text)
        collector = GoogleNewsCollector()
        with patch.object(collector, '_request', return_value=response):
            self.assertIn('검증기업', collector._fetch_publisher_snippet(response.url, '신규 계약'))

    def test_image_description_and_caption_are_not_article_text(self):
        body = '검증기업은 해외 고객사와 신규 공급계약을 체결했다고 밝혔다.'
        html = '<script type="application/ld+json">' + json.dumps({
            '@type':'NewsArticle', 'headline':'검증기업', 'image':{'@type':'ImageObject','description':'사진 속 모델이 신제품을 소개하고 있는 모습이다.'},
            'description':body}, ensure_ascii=False) + '</script>'
        self.assertEqual(_extract_article_snippet(html, '검증기업'), body)
        dom = f'<article itemprop="articleBody"><figure><p>사진 속 모델이 신제품을 소개하고 있는 모습이다.</p></figure><p>{body}</p></article>'
        self.assertEqual(_extract_article_snippet(dom, '검증기업'), body)

    def test_organization_and_related_article_descriptions_are_not_current_article(self):
        payload = {'@graph': [
            {'@type':'NewsMediaOrganization','description':'언론사의 서비스 소개이며 실제 기사 내용과 무관한 설명이다.'},
            {'@type':'NewsArticle','headline':'다른 기사','description':'다른 회사의 신규 계약에 관한 기사 설명이다.'},
            {'@type':['Article','NewsArticle'],'headline':'대상 기사','description':'대상 기업의 신규 공급계약에 관한 기사 설명이다.',
             'publisher':{'description':'기사에 연결된 언론사에 대한 설명문이다.'}},
        ]}
        html = '<script type="application/ld+json">'+json.dumps(payload,ensure_ascii=False)+'</script>'
        self.assertEqual(_extract_article_snippet(html,'대상 기사'),payload['@graph'][2]['description'])
        self.assertEqual(_extract_article_snippet(html,'존재하지 않는 기사'),'')

    def test_article_url_matches_without_borrowing_other_story(self):
        payload = {'@type':'NewsArticle','url':'https://example.com/current#article',
                   'articleBody':'기사 제목이 갱신되어도 같은 주소의 기사 본문을 읽는다.'}
        html = '<script type="application/ld+json">'+json.dumps(payload,ensure_ascii=False)+'</script>'
        self.assertTrue(_extract_article_snippet(html,'옛 제목','https://example.com/current'))
        self.assertEqual(_extract_article_snippet(html,'옛 제목','https://example.com/other'),'')

    def test_nested_story_card_is_removed_from_explicit_body(self):
        body = '종근당은 부위별 특성에 맞는 여드름 치료제 두 종류를 출시했다.'
        html = f'''<main><article><p>페이지 다른 위치의 추천 기사 제목을 잘못 읽으면 안 된다.</p></article>
        <section class="article-body"><div class="series-news"><ul><li><article>
        <p class="title">현대차그룹은 협력사 납품대금을 조기 지급한다고 발표했다.</p>
        </article></li></ul></div><p>{body}</p></section></main>'''
        self.assertEqual(_extract_article_snippet(html,'종근당 신제품'),body)
        self.assertEqual(_extract_article_snippet('<main><p>이 문장은 기사 본문이 아니라 페이지의 공통 안내문이다.</p></main>','제목'),'')

    def test_meta_requires_matching_article_identity(self):
        html = '<meta property="og:type" content="website"><meta property="og:title" content="대상 기사"><meta property="og:description" content="이 문장은 홈페이지에 관한 서비스 안내문이다.">'
        self.assertEqual(_extract_article_snippet(html,'대상 기사'),'')
        html=html.replace('website','article')
        self.assertEqual(_extract_article_snippet(html,'다른 기사'),'')
        self.assertTrue(_extract_article_snippet(html,'대상 기사'))

    def test_unavailable_body_is_not_replaced_with_service_description(self):
        html = '''<meta property="og:type" content="article"><meta property="og:title" content="대상 기사">
        <meta property="og:description" content="이 기사는 금융정보 단말기에서 서비스된 기사입니다.">
        <article itemprop="articleBody"><p>Copyright 무단 전재 금지</p>
        <p>이 기사는 금융정보 단말기에서 서비스된 기사입니다.</p></article>'''
        self.assertEqual(_extract_article_snippet(html,'대상 기사'),'')

    def test_old_policy_excerpt_is_refetched_not_relabelled(self):
        full=report(['a'])
        for view in ('news_events_all','news_events_weekly','news_events_final'):
            full[view][0]['representative'].update(snippet='이전에 잘못 추출된 언론사 공통 소개문이다.',
                snippet_metadata={'snippet_policy':'short_excerpt_v1','snippet_source':'publisher_article_excerpt'})
        collector=self.collector()
        enrich_report_snippets([full],collector=collector)
        self.assertEqual(collector._fetch_publisher_snippet.call_count,1)
        self.assertIn('계약 기간',full['news_events_final'][0]['representative']['snippet'])

    def test_cms_body_scopes_do_not_depend_on_og_article_type(self):
        body = '검증기업의 연결 매출은 증가했지만 영업이익률은 하락했다.'
        wrappers = [
            f'<div id="newsContent" class="news_content ck-content"><p>{body}</p></div>',
            f'<div class="content_print"><div class="contarea">{body}<br></div></div>',
            f'<article><div id="boardContent">{body}<br></div></article>',
        ]
        for wrapper in wrappers:
            with self.subTest(wrapper=wrapper):
                html = '<meta property="og:type" content="website">' + wrapper
                self.assertEqual(_extract_article_snippet(html, '검증기업 실적'), body)
        self.assertEqual(_extract_article_snippet(
            f'<div class="contarea">{body}</div>', '검증기업 실적'), '')

    def test_editor_content_scope_excludes_title_quote_and_related_card(self):
        body = '검증기업은 기존 유통계약을 종료하고 새로운 거래처와 계약했다.'
        html = f'''<div class="se-main-container">
          <div class="se-section-documentTitle">이 부분은 기사 제목이다.</div>
          <div class="se-section-quotation">이 부분은 별도 인용 제목이다.</div>
          <div class="se-section-text"><p>{body}</p></div>
          <div class="se-section-oglink">관계없는 주변 기사 설명을 사용하면 안 된다.</div>
        </div>'''
        self.assertEqual(_extract_article_snippet(html, '대상 기사'), body)

    def test_declared_publisher_affix_is_allowed_but_other_titles_are_not(self):
        body = '검증기업은 해외 시장 진출을 위한 연구개발 투자를 확대했다.'
        for heading, requested in [('대상 기사', '대상 기사 - 검증일보'),
                                   ('[검증일보]대상 기사', '대상 기사')]:
            html = f'''<meta property="og:type" content="article">
            <meta property="og:site_name" content="검증일보">
            <meta property="og:title" content="{heading}">
            <meta property="og:description" content="{body}">'''
            self.assertEqual(_extract_article_snippet(html, requested), body)
            self.assertEqual(_extract_article_snippet(html, '다른 기사'), '')
            self.assertEqual(_extract_article_snippet(html, '대상 기사 전망 수정'), '')
            self.assertEqual(_extract_article_snippet(html.replace('article', 'website'), requested), '')
            self.assertEqual(_extract_article_snippet(
                html.replace('<meta property="og:site_name" content="검증일보">', ''), requested), '')

    def test_explicit_leading_editor_note_is_skipped_not_ordinary_evidence(self):
        body = '검증기업은 첫 연간 흑자를 기록하고 해외 판매를 확대하고 있다.'
        html = f'''<div class="view_con_wrap"></div><div class="view_con_wrap">
        <p>이 연재의 구성과 자료 출처를 독자에게 설명하는 문장이다. <strong>&lt;편집자 주&gt;</strong></p>
        <figure><figcaption>사진 설명을 기사 내용으로 사용해서는 안 된다.</figcaption></figure>
        <p>{body}</p></div>'''
        self.assertEqual(_extract_article_snippet(html, '검증기업 실적'), body)
        ordinary = f'<div class="view_con_wrap"><p>{body}</p></div>'
        self.assertEqual(_extract_article_snippet(ordinary, '검증기업 실적'), body)
        unmarked = '이 문장이 여러 기사에 반복되더라도 실제 사건의 근거일 수 있다.'
        self.assertTrue(_extract_article_snippet(
            f'<div class="article-body"><p>{unmarked}</p><p>{body}</p></div>',
            '검증기업 실적').startswith(unmarked))

    def test_v2_policy_is_refetched_for_both_conditions(self):
        full, random = report(['a', 'b']), report(['b', 'c'])
        for r in [full, random]:
            for view in ('news_events_all', 'news_events_weekly', 'news_events_final'):
                for e in r[view]:
                    e['representative'].update(snippet='이전 정책으로 수집한 기사 내용이며 다시 추출해야 한다.',
                        snippet_metadata={'snippet_policy':'short_excerpt_v2_article_scope'})
        collector = self.collector()
        enrich_report_snippets([full, random], collector=collector)
        self.assertEqual(collector._fetch_publisher_snippet.call_count, 3)
        self.assertEqual(full['news_events_weekly'][1]['representative'],
                         random['news_events_weekly'][0]['representative'])

    def test_long_lead_keeps_context_and_exposes_first_section(self):
        intro = '여러 산업을 다루는 연재의 배경과 자료 범위를 설명하는 문장이다. ' * 12
        detail = '첫 제품은 2020년 출시됐으며 2023년 생산액은 8억원으로 집계됐다.'
        for heading in ['<h2>첫 제품</h2>', '<p><strong>■첫 제품</strong></p>',
                        '<div role="heading">첫 제품</div>']:
            with self.subTest(heading=heading):
                html = f'<div class="article-body"><p>{intro}</p>{heading}<p>{detail}</p></div>'
                snippet = _extract_article_snippet(html, '연재 기사')
                self.assertTrue(snippet.startswith('여러 산업을 다루는 연재'))
                self.assertIn(' […] ', snippet)
                self.assertIn(detail, snippet)
                self.assertLessEqual(len(snippet), 280)
                lead, section = snippet.split(' […] ')
                self.assertIn(lead.removesuffix('...'), intro)
                self.assertIn(section.removesuffix('...'),
                              ('■' if '<strong>' in heading else '') + '첫 제품 ' + detail)

    def test_repeated_lead_does_not_collapse_independent_articles(self):
        intro = '같은 연재에서 사용하는 배경 설명이지만 문장을 임의로 삭제하지 않는다. ' * 12
        excerpts = []
        for product, amount in [('제품가', '9억원'), ('제품나', '6억원')]:
            html = f'''<div class="article-body"><p>{intro}</p>
            <p><strong>■{product}</strong></p><p>{product}는 국내에서 생산되며 지난해 생산액은 {amount}으로 집계됐다.</p></div>'''
            excerpts.append(_extract_article_snippet(html, '제품 연재'))
            self.assertIn(product, excerpts[-1])
            self.assertIn(amount, excerpts[-1])
        self.assertNotEqual(*excerpts)

    def test_excerpt_does_not_choose_a_later_more_positive_section(self):
        intro = '기업 실적을 해석하는 데 필요한 배경과 이전 경과를 설명하는 문장이다. ' * 12
        html = f'''<div class="article-body"><p>{intro}</p>
        <h2>손실 발생</h2><p>{'신규 사업에서 손실이 발생해 비용 부담이 증가했다. ' * 12}</p>
        <h2>매출 급증</h2><p>매출이 급증하고 수익성이 크게 개선됐다.</p></div>'''
        snippet = _extract_article_snippet(html, '기업 실적')
        self.assertIn('손실 발생', snippet)
        self.assertNotIn('매출 급증', snippet)

    def test_no_heading_and_short_lead_keep_existing_excerpt(self):
        text = '실제 뉴스의 중요한 사실은 여러 기사에서 반복될 수 있으므로 보존해야 한다. ' * 12
        self.assertEqual(_extract_article_snippet(
            f'<div class="article-body"><p>{text}</p></div>', '대상 기사'), _truncate_snippet(text))
        html = '<div class="article-body"><p>회사는 새로운 계약을 발표했다.</p><h2>계약 내용</h2><p>이번 계약의 기간은 3년이고 거래 금액은 100억원이다.</p></div>'
        self.assertNotIn(' […] ', _extract_article_snippet(html, '대상 기사'))

    def test_inline_emphasis_and_related_headings_do_not_trigger_section_excerpt(self):
        intro = '기사의 중요한 사실이 이어지므로 단순한 강조를 소제목으로 보아서는 안 된다. ' * 12
        detail = '<p><strong>강조된 문장도 사실을 전달하는 일반 문장일 수 있다.</strong></p>'
        html = f'''<div class="article-body"><p>{intro}</p>{detail}
        <aside><h2>주변 기사</h2><p>관계없는 기업의 이익이 크게 증가했다.</p></aside></div>'''
        self.assertEqual(_extract_article_snippet(html, '대상 기사'), _truncate_snippet(intro))

    def test_empty_section_does_not_replace_useful_lead(self):
        intro = '검증기업은 수익성이 악화된 사업을 매각하고 차입금을 상환했다고 발표했다. ' * 12
        html = f'<div class="article-body"><p>{intro}</p><h2>관련 자료</h2></div>'
        self.assertEqual(_extract_article_snippet(html, '대상 기사'), _truncate_snippet(intro))

    def test_v3_policy_is_refetched_not_silently_reused(self):
        full = report(['a'])
        for view in ('news_events_all', 'news_events_weekly', 'news_events_final'):
            full[view][0]['representative'].update(snippet='이전 정책으로 추출된 긴 소개문의 첫 부분이다.',
                snippet_metadata={'snippet_policy': 'short_excerpt_v3_body_identity'})
        collector = self.collector()
        enrich_report_snippets([full], collector=collector)
        self.assertEqual(collector._fetch_publisher_snippet.call_count, 1)

    def test_heading_marker_placement_keeps_first_company_in_roundup(self):
        detail = '검증기업은 평가에서 최고 등급을 받았으며 전년 대비 등급이 상승했다. ' * 12
        headings = ['<p><strong>◆검증기업 평가</strong></p>',
                    '<p>◆<strong>검증기업 평가</strong></p>',
                    '<p><span>◆ </span><b>검증기업 평가</b></p>',
                    '<p><strong>◆</strong><strong>검증기업 평가</strong></p>',
                    '<p>◆<strong>검증기업</strong> <b>평가</b></p>']
        for heading in headings:
            with self.subTest(heading=heading):
                html = f'''<div class="article-body">{heading}<p>{detail}</p>
                <p><strong>◆다른기업 카페 개점</strong></p>
                <p>다른기업은 공장 안에 새로운 카페를 개점하고 행사를 개최했다.</p></div>'''
                snippet = _extract_article_snippet(html, '기업 소식 모음')
                self.assertIn('최고 등급', snippet)
                self.assertNotIn(' […] ', snippet)
                self.assertNotIn('다른기업', snippet)

    def test_heading_marker_placement_also_works_after_long_intro(self):
        intro = '연재 구성에 대한 배경 설명이며 기사마다 공통으로 사용하는 도입 문장이다. ' * 12
        for heading in ['<p>■<strong>첫 제품</strong></p>',
                        '<p><strong>■</strong><b>첫 제품</b></p>']:
            html = f'''<div class="article-body"><p>{intro}</p>{heading}
            <p>첫 제품은 지난해 국내에서 생산됐으며 연간 생산액은 8억원이다.</p></div>'''
            snippet = _extract_article_snippet(html, '제품 연재')
            self.assertIn(' […] ', snippet)
            self.assertIn('8억원', snippet)
            self.assertLessEqual(len(snippet), 280)

    def test_marker_with_partial_bold_prose_is_not_a_heading(self):
        intro = '기존 기사에서 확인되는 사실을 다른 문단으로 대체하지 않고 보존한다. ' * 12
        for paragraph in ['<p>◆<strong>기업</strong>은 새로운 계약을 발표했다.</p>',
                          '<p>◆새로운 소식을 <strong>기업</strong>이 발표했다.</p>']:
            html = f'<div class="article-body"><p>{intro}</p>{paragraph}</div>'
            self.assertEqual(_extract_article_snippet(html, '기업 소식'), _truncate_snippet(intro))

    def test_v4_policy_is_refetched_not_relabelled(self):
        full = report(['a'])
        for view in ('news_events_all', 'news_events_weekly', 'news_events_final'):
            full[view][0]['representative'].update(snippet='기호 태그를 잘못 읽은 이전 정책의 발췌문이다.',
                snippet_metadata={'snippet_policy': 'short_excerpt_v4_section_excerpt'})
        collector = self.collector()
        enrich_report_snippets([full], collector=collector)
        self.assertEqual(collector._fetch_publisher_snippet.call_count, 1)


if __name__ == '__main__':
    unittest.main()
