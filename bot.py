"""Daily public-vacancy digest. No paid API or employer contact."""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlsplit, quote
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'state' / 'seen.json'
SKILLS = {'Excel': r'\bexcel\b', 'Power Query': r'power\s*query',
          'Power BI': r'power\s*bi', 'SQL': r'\b(?:ms\s*)?sql\b'}
LEFT = r'лівобереж|левобереж|дарницьк|дарницк|позняк|осокорк|троєщ|троещ|харківськ|харьковск|чавдар|бажана|броварськ|броварск|дніпровськ|днепровск'
RIGHT = r'подільськ|подольск|оболон|голосіїв|голосеев|печерськ|печерск|солом.ян|святошин|шевченківськ|шевченковск|кирилівськ|кирилловск|електриків|электриков|жилянськ|жилянск|деміїв|демиев|васильків|васильков|контрактов|героїв дніпра|героев днепра'
ROLE = r'аналітик|аналитик|data\s*analyst|sales\s*analyst|research\s*analyst'
EXCLUDE = r'business\s*analyst|бізнес.аналітик|бизнес.аналитик|системн\w*.аналітик|системн\w*.аналитик|senior|lead\s*data|керівник|руководитель|хімік|химик|військова служба|військовослужбовець'

def text_only(value):
    return html.unescape(re.sub(r'<[^>]+>', ' ', str(value or '')))

def postings(value):
    if isinstance(value, list):
        for item in value:
            yield from postings(item)
    elif isinstance(value, dict):
        types = value.get('@type', [])
        if types == 'JobPosting' or isinstance(types, list) and 'JobPosting' in types:
            yield value
        if '@graph' in value:
            yield from postings(value['@graph'])

def locations(job):
    locs = job.get('jobLocation', [])
    if isinstance(locs, dict): locs = [locs]
    parts = []
    for loc in locs:
        a = loc.get('address', {})
        if isinstance(a, dict):
            parts.append(', '.join(str(a[k]) for k in ['addressLocality', 'streetAddress', 'addressRegion'] if a.get(k)))
        elif isinstance(a, str): parts.append(a)
    return '; '.join(parts)

def visible_posting(title, body, page_title, url, now=None):
    """Conservative fallback; never infer a fresh date from today's date."""
    lines = [s.strip() for s in body.splitlines() if s.strip()]
    months = {'січня':1,'января':1,'лютого':2,'февраля':2,'березня':3,'марта':3,
      'квітня':4,'апреля':4,'травня':5,'мая':5,'червня':6,'июня':6,
      'липня':7,'июля':7,'серпня':8,'августа':8,'вересня':9,'сентября':9,
      'жовтня':10,'октября':10,'листопада':11,'ноября':11,'грудня':12,'декабря':12}
    date = ''
    header = '\n'.join(lines[:65])
    match = re.search(r'\b(\d{1,2})\s+('+'|'.join(months)+r')\s+(20\d{2})\b',header,re.I)
    if match:
        try: date = dt.date(int(match[3]),months[match[2].lower()],int(match[1])).isoformat()
        except ValueError: return None
    date_source = 'дата в тексте сайта; значение не установлено'
    if not date:
        clock = now or dt.datetime.now(ZoneInfo('Europe/Kyiv'))
        relative = re.search(r'\b(\d+)\s*(хвилин\w*|минут\w*|годин\w*|час\w*|дн\w*|день|тижн\w*|недел\w*)\s+(?:тому|назад)\b',header,re.I)
        if relative:
            number, unit = int(relative[1]), relative[2].lower()
            if unit.startswith(('хвилин','минут')): delta = dt.timedelta(minutes=number)
            elif unit.startswith(('годин','час')): delta = dt.timedelta(hours=number)
            elif unit.startswith(('тижн','недел')): delta = dt.timedelta(weeks=number)
            else: delta = dt.timedelta(days=number)
            date = (clock-delta).date().isoformat()
            date_source = 'приблизительная дата обновления: '+relative[0]
        elif re.search(r'\b(?:сьогодні|сегодня)\b',header,re.I):
            date = clock.date().isoformat()
            date_source = 'обновление сегодня по тексту сайта'
        elif re.search(r'\b(?:вчора|вчера)\b',header,re.I):
            date = (clock-dt.timedelta(days=1)).date().isoformat()
            date_source = 'обновление вчера по тексту сайта'
    if not date: return None
    company = re.search(r'\s[-–]\s(.+?)\s*\|\s*(?:robota\.ua|Work\.ua)',page_title,re.I)
    city_index = next((i for i,s in enumerate(lines[:65]) if re.match(r'^(Київ|Киев|Kyiv)',s,re.I)), None)
    address = lines[city_index] if city_index is not None else ''
    employer = company[1] if company else (lines[city_index+1] if city_index is not None and city_index+1 < len(lines) else 'Компания требует проверки')
    salary_line = next((s for s in lines[:city_index or 20] if re.search(r'\d.*(?:₴|грн)',s)), '')
    desc = re.split(r'Гарячі вакансії|Схожі вакансії|Похожие вакансии|Рекомендовані вакансії',body, maxsplit=1)[0]
    return {'title':title,'description':desc,'url':url,'datePosted':date,
      'hiringOrganization':{'name':employer},
      'jobLocation':{'address':{'streetAddress':address}}, '_date_source':date_source,
      '_salary_text':salary_line, '_format': 'гибрид' if re.search(r'гібрид|гибрид',header,re.I) else 'офис' if 'В офісі/на місці' in header else 'формат уточнить'}

def assess(job):
    title = str(job.get('title', ''))
    desc = text_only(job.get('description', ''))
    low = desc.lower()
    if not re.search(ROLE, title, re.I) or re.search(EXCLUDE, title, re.I): return None
    address = locations(job)
    remote = job.get('jobLocationType') == 'TELECOMMUTE' or bool(re.search(r'повністю віддален|полностью удаленн|fully remote', low))
    hybrid = bool(re.search(r'гібрид|гибрид|hybrid', low))
    if hybrid: remote = False
    if re.search(LEFT, address, re.I) and not remote: return None
    if address and not re.search(r'київ|киев|kyiv|kiev', address, re.I) and not remote: return None
    if remote:
        location_status = 'Удалённо; доступность работы из Киева уточнить'
    elif re.search(RIGHT, address, re.I):
        location_status = 'Правый берег (по опубликованному адресу)'
    else:
        location_status = 'Локация требует уточнения'
    skills = {}
    score = 0
    gaps = []
    for name, pattern in SKILLS.items():
        match = re.search(pattern, low)
        if not match:
            skills[name] = 'не упомянут'
            continue
        context = low[max(0, match.start()-90):match.end()+140]
        preferred = bool(re.search(r'переваг|бажан|желател|буде плюсом|preferred|nice.to.have', context))
        skills[name] = 'преимущество' if preferred else 'упомянут; обязательность уточнить'
        if name in ('Excel', 'Power Query'): score += 3
        if name in ('SQL', 'Power BI') and not preferred:
            if re.search(r'висок|высок|просунут|advanced|складн|сложн|expert', context):
                gaps.append(name + ': высокий уровень')
                score -= 4
            else:
                gaps.append(name + ': проверить требуемый уровень')
                score -= 1
    sector = 'не установлен'
    for label, pattern in [('фарма', r'фарма|аптек|лікарськ'), ('производство', r'виробниц|производств'),
                           ('страхование', r'страхова компан|страхової компан|insurance company'),
                           ('исследования', r'дослідж|исследован|research')]:
        if re.search(pattern, low):
            sector = label
            score += 3
            break
    if re.search(r'статист|методич|методолог', low): score += 2
    if remote or 'Правый' in location_status: score += 1
    date = job.get('datePosted', '')
    try:
        published = dt.date.fromisoformat(str(date)[:10])
    except ValueError:
        return None  # Never label an undated page as a fresh vacancy.
    age = (dt.datetime.now(ZoneInfo('Europe/Kyiv')).date() - published).days
    if age < 0 or age > 14: return None
    expiry = job.get('validThrough')
    if expiry:
        try:
            if dt.date.fromisoformat(str(expiry)[:10]) < dt.datetime.now(ZoneInfo('Europe/Kyiv')).date(): return None
        except ValueError: pass
    if age <= 7: score += 1
    employer = job.get('hiringOrganization', {}).get('name', 'Компания не указана')
    salary = job.get('baseSalary')
    salary_text = job.get('_salary_text') or 'не указана'
    if isinstance(salary, dict):
        value = salary.get('value', {})
        if isinstance(value, dict):
            bounds = [str(value[k]) for k in ('minValue', 'maxValue') if value.get(k) is not None]
            if not bounds and value.get('value') is not None: bounds = [str(value['value'])]
            if bounds: salary_text = '–'.join(bounds) + ' ' + str(salary.get('currency', '')) + '; налоги не уточнены'
    return dict(title=title, employer=employer, url=job['url'], date=str(date)[:10], age=age,
                date_source=job.get('_date_source','дата из метаданных; значение не установлено'),
                address=address or 'не опубликован', location=location_status,
                format='гибрид' if hybrid else 'удалённо' if remote else job.get('_format','формат уточнить'),
                salary=salary_text, sector=sector, skills=skills, score=score, gaps=gaps)

def telegram(method, payload):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not token: raise RuntimeError('Добавьте секрет TELEGRAM_BOT_TOKEN')
    req = urllib.request.Request('https://api.telegram.org/bot'+token+'/'+method,
         data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.load(response)
    except Exception:
        raise RuntimeError('Ошибка Telegram. Проверьте токен, chat ID и кнопку Start у бота.') from None
    if not result.get('ok'): raise RuntimeError('Telegram отклонил запрос')
    return result.get('result')

def send(text):
    chat = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not re.fullmatch(r'\d+', chat): raise RuntimeError('Добавьте секрет TELEGRAM_CHAT_ID: числовой ID вашего личного чата')
    for start in range(0, len(text), 3500):
        telegram('sendMessage', {'chat_id': chat, 'text': text[start:start+3500],
                 'link_preview_options': {'is_disabled': True}})

def collect():
    from playwright.sync_api import sync_playwright
    errors, found = [], {}
    sources = json.loads((ROOT / 'sources.json').read_text())
    extra = [
      ('robota.ua — аналитика продаж','https://robota.ua/zapros/analityk-prodazhiv/kyiv'),
      ('robota.ua — Data Analyst','https://robota.ua/zapros/data-analyst/kyiv'),
      ('robota.ua — консолидированная информация','https://robota.ua/zapros/analityk-konsolidovanoyi-informatsiyi/kyiv')]
    for name,url in extra:
        if not any(s['url'] == url for s in sources): sources.append({'name':name,'url':url,'selector':"a[href*='/vacancy']"})
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(locale='uk-UA')
        page = context.new_page()
        for source in sources:
            try:
                response = page.goto(source['url'], wait_until='domcontentloaded', timeout=45000)
                if response and response.status >= 400: raise RuntimeError('HTTP '+str(response.status))
                try: page.wait_for_selector(source['selector'], timeout=12000)
                except Exception: pass
                if 'robota.ua' in source['url']:
                    try: page.wait_for_function("document.querySelectorAll(\"a[href*='/vacancy']\").length >= 15", timeout=15000)
                    except Exception: pass
                links = page.locator(source['selector']).evaluate_all('(els) => els.map(e => e.href)')
                links = list(dict.fromkeys(links))[:25]
                if not links: raise RuntimeError('Нет доступных ссылок')
                parsed_count = 0
                detail_errors = 0
                for link_index, url in enumerate(links):
                    if url in found: continue
                    if urlsplit(url).hostname != urlsplit(source['url']).hostname: continue
                    try:
                        response = page.goto(url, wait_until='domcontentloaded', timeout=35000)
                        if response and response.status >= 400:
                            detail_errors += 1
                            continue
                        try: page.wait_for_selector('h1', timeout=12000)
                        except Exception: pass
                        try: page.wait_for_selector('script[type="application/ld+json"]', state='attached', timeout=5000)
                        except Exception: pass
                        scripts = page.locator('script[type="application/ld+json"]').all_text_contents()
                        page_jobs = []
                        for script in scripts:
                            try: data = json.loads(script)
                            except ValueError: continue
                            page_jobs.extend(postings(data))
                        if not page_jobs and page.locator('h1').count():
                            job = visible_posting(page.locator('h1').first.inner_text(),page.locator('body').inner_text(),page.title(),url)
                            if job: page_jobs.append(job)
                        for job in page_jobs:
                                job['url'] = url
                                parsed_count += 1
                                result = assess(job)
                                if result: found[url] = result
                    except Exception:
                        detail_errors += 1
                        continue
                diagnostic = f"ссылок {len(links)}, описаний {parsed_count}, ошибок страниц {detail_errors}"
                print(source['name']+': '+diagnostic)
                if not parsed_count: errors.append(source['name'] + ': '+diagnostic+'; вакансии не прочитаны')
            except Exception as error:
                status = str(error) if isinstance(error, RuntimeError) else type(error).__name__
                errors.append(source['name'] + ': сбор недоступен; '+status)
        browser.close()
    from sources_extra import collect_extra
    more, notes = collect_extra(assess)
    for job in more:found.setdefault(job['url'],job)
    errors.extend(notes)
    unique={}
    for job in found.values():
        key=(job['employer'].lower().strip(),job['title'].lower().strip())
        unique.setdefault(key,job)
    return list(unique.values()), errors

def report(jobs, errors, seen):
    today = dt.datetime.now(ZoneInfo('Europe/Kyiv')).date().isoformat()
    main = [j for j in jobs if 'Локация требует' not in j['location'] and j['score'] >= 3]
    pending = [j for j in jobs if 'Локация требует' in j['location'] and j['score'] >= 3]
    rank = lambda j: (j['url'] not in seen, j['score'], -j['age'])
    main.sort(key=rank, reverse=True)
    pending.sort(key=rank, reverse=True)
    selected = main[:15]
    lines = ['Вакансии аналитика · '+today, 'Бесплатная оценка по правилам, без AI.',
             'Дата с сайта (метаданные или текст); первая публикация/обновление не различены.']
    if selected and any(j['age'] > 7 for j in selected): lines.append('Поиск расширен до 14 дней.')
    def card(j, i):
        label = 'повтор из предыдущих отправок' if j['url'] in seen else 'впервые в отправках этого бота'
        fit = 'высокое по правилам' if j['score'] >= 8 and not j['gaps'] else 'среднее по правилам'
        return '\n'.join([f"\n{i}. {j['employer']} — {j['title']}",
            f"{j['date']} ({j.get('date_source','дата сайта')}) · {label} · отрасль: {j['sector']}",
            f"{j['format']} · {j['address']} · {j['location']}",
            'Зарплата: '+j['salary'],
            '; '.join(k+': '+v for k,v in j['skills'].items()),
            'Соответствие: '+fit+'; '+('; '.join(j['gaps']) or 'основной плюс: навыки обработки данных'), j['url']])
    for i,j in enumerate(selected, 1): lines.append(card(j,i))
    if not selected: lines.append('\nСбор не дал подходящих вакансий. При ошибках источников результат поиска неполный; это не означает отсутствия вакансий.')
    if pending:
        lines.append('\nЛОКАЦИЯ ТРЕБУЕТ УТОЧНЕНИЯ — вне основного топа')
        for i,j in enumerate(pending[:5],1): lines.append(card(j,i))
    if selected: lines.append('\nПроверить в первую очередь: '+', '.join(j['employer'] for j in selected[:3]))
    lines.append('\nПроверяются: robota.ua, Work.ua, DOU, Happy Monday, Jooble (с проверкой первоисточника). Telegram-каналы и Djinni пока не подключены. Национальность компании, метро и время в пути автоматически не подтверждаются.')
    if errors: lines.append('\nСтатус источников (при ошибках поиск неполный):\n'+'\n'.join(errors))
    return '\n'.join(lines), selected + pending[:5]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--test-message', action='store_true')
    args = parser.parse_args()
    if args.test_message:
        send('Тест: бот вакансий подключён. Это проверка доставки, поиск ещё не выполнялся.')
        print('Тестовое сообщение отправлено')
        return
    if not args.dry_run and (not os.environ.get('TELEGRAM_BOT_TOKEN') or not os.environ.get('TELEGRAM_CHAT_ID')):
        raise RuntimeError('Сначала добавьте TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в GitHub Secrets')
    seen = json.loads(STATE.read_text()) if STATE.exists() else {}
    jobs, errors = collect()
    message, selected = report(jobs, errors, seen)
    if args.dry_run: print(message)
    else:
        send(message)
        stamp = dt.datetime.now(dt.timezone.utc).date().isoformat()
        for job in selected: seen[job['url']] = stamp
        cutoff = (dt.datetime.now(dt.timezone.utc).date()-dt.timedelta(days=45)).isoformat()
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps({u:d for u,d in seen.items() if d >= cutoff}))
        print('Подборка отправлена; подходящих объявлений:', len(selected))

if __name__ == '__main__':
    try: main()
    except Exception as e:
        # Never include request URLs (bot token) or remote HTML in logs.
        print(str(e) if isinstance(e, RuntimeError) else 'Ошибка запуска; проверьте настройки и источники', file=sys.stderr)
        sys.exit(1)
