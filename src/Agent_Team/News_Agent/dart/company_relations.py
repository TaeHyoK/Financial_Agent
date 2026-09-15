"""Read company relationships from bounded DART detail sections, without inference."""
import re
import unicodedata

from bs4 import BeautifulSoup

from Agent_Team.Financial_Agent.table_parser import parse_table_matrix


def name_key(text):
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    return re.sub(r"\s+|주식회사|농업회사법인|\(주\)", "", text)


def _compact(text):
    return re.sub(r"\s+", "", text)


def prefix_aliases(target_names, company_names):
    """Derive spelling variants from target aliases, only for disclosed names.

    For example, confirmed AB전자/에이비전자 aliases supply AB/에이비 spelling
    correspondence. This is a naming inference, not a new ownership relation;
    never override a different company name already present in the filing.
    """
    pairs = set()
    for name in target_names:
        match = re.fullmatch(r'([a-z]+)([가-힣].+)', name)
        if not match:
            continue
        latin, suffix = match.groups()
        for alias in target_names:
            if alias != name and alias.endswith(suffix):
                korean = alias[:-len(suffix)]
                if re.fullmatch(r'[가-힣]+', korean):
                    pairs.update([(latin, korean), (korean, latin)])
    proposals = {}
    for name in company_names:
        for prefix, replacement in pairs:
            if name.startswith(prefix):
                suffix = name[len(prefix):]
                if suffix and not re.match(r'[가-힣]', suffix):
                    continue
                alias = replacement + suffix
                if alias not in company_names:
                    proposals.setdefault(alias, set()).add(name)
    return {alias: next(iter(names)) for alias, names in proposals.items() if len(names) == 1}


def read_company_relations(xml_text):
    """Use column labels and registration numbers, not fixed column positions.

    DART mixes XML with HTML-style markup. Bound each section before parsing so
    malformed text elsewhere cannot hide the relationship tables. Missing or
    unreadable sections raise; an explicitly empty table is a different result.
    """
    header_tags = re.findall(r'<(?:COMPANY-NAME|EXTRACTION)\b[^>]*>.*?</(?:COMPANY-NAME|EXTRACTION)>',
                             xml_text, re.S | re.I)
    header = BeautifulSoup(''.join(header_tags), 'html.parser')
    legal = header.find('company-name')
    registration = header.find('extraction', attrs={'acode': 'CRP_RGS_NO_TEMP'})
    target_registration = re.sub(r'\D', '', registration.get_text()) if registration else ''
    titles = {'affiliates': '계열회사현황(상세)', 'subsidiaries': '연결대상종속회사현황(상세)'}
    sections = {key: [] for key in titles}
    for match in re.finditer(r'<SECTION-2\b[^>]*>.*?</SECTION-2>', xml_text, re.S | re.I):
        fragment = match.group()
        title = re.search(r'<TITLE\b[^>]*>(.*?)</TITLE>', fragment, re.S | re.I)
        if title is None:
            continue
        heading = _compact(BeautifulSoup(title.group(1), 'html.parser').get_text())
        for key, label in titles.items():
            if heading.endswith(label):
                sections[key].append(fragment)
    names, registrations, audit = {}, {}, {}
    for kind, fragments in sections.items():
        if not fragments:
            raise ValueError(f'DART company relations: missing {titles[kind]} section')
        found, tables, empty_rows = set(), 0, 0
        for fragment in fragments:
            soup = BeautifulSoup(fragment, 'html.parser')
            for table in soup.find_all('table'):
                matrix = parse_table_matrix(table)
                name_col = registration_col = None
                for row in matrix:
                    labels = [_compact(cell) for cell in row]
                    header_cols = [i for i, cell in enumerate(labels)
                                   if cell in {'상호', '기업명', '회사명', '계열회사명'}]
                    if header_cols:
                        name_col = header_cols[0]
                        registration_col = labels.index('법인등록번호') if '법인등록번호' in labels else None
                        tables += 1
                        continue
                    if name_col is None:
                        continue
                    name = row[name_col].strip()
                    if not name and any(cell.strip() for cell in row):
                        raise ValueError(f'DART company relations: unreadable {titles[kind]} company-name cell')
                    if _compact(name) in {'', '-', '—', '해당없음', '해당사항없음'}:
                        empty_rows += 1
                        continue
                    if _compact(name) in {'계', '합계', '소계', '총계'}:
                        continue
                    found.add(name)
                    if registration_col is not None:
                        number = re.sub(r'\D', '', row[registration_col])
                        if len(number) == 13:
                            registrations.setdefault(number, set()).add(name)
        if not tables or not (found or empty_rows):
            raise ValueError(f'DART company relations: unreadable {titles[kind]} table')
        names[kind] = found
        audit[kind] = {'status': 'parsed' if found else 'empty', 'sections': len(fragments),
                       'tables': tables, 'company_count': len(found)}
    return {'legal_name': legal.get_text(' ', strip=True) if legal else '',
            'target_registration': target_registration,
            'names': names, 'registrations': registrations, 'tables': audit}
