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
    salary_text = 'не указана'
    if isinstance(salary, dict):
        value = salary.get('value', {})
        if isinstance(value, dict):
            bounds = [str(value[k]) for k in ('minValue', 'maxValue') if value.get(k) is not None]
            if not bounds and value.get('value') is not None: bounds = [str(value['value'])]
            if bounds: salary_text = '–'.join(bounds) + ' ' + str(salary.get('currency', '')) + '; налоги не уточнены'
    return dict(title=title, employer=employer, url=job['url'], date=str(date)[:10], age=age,
                address=address or 'не опубликован', location=location_status,
                format='гибрид' if hybrid else 'удалённо' if remote else 'формат уточнить',
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
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(locale='uk-UA')
        page = context.new_page()
        for source in sources:
            try:
                response = page.goto(source['url'], wait_until='domcontentloaded', timeout=45000)
                if response and response.status >= 400: raise RuntimeError('HTTP')
                try: page.wait_for_selector(source['selector'], timeout=12000)
                except Exception: pass
                links = page.locator(source['selector']).evaluate_all('(els) => els.map(e => e.href)')
                links = list(dict.fromkeys(links))[:25]
                if not links: raise RuntimeError('Нет доступных ссылок')
                parsed_count = 0
                for url in links:
                    if url in found: continue
                    if urlsplit(url).hostname != urlsplit(source['url']).hostname: continue
                    try:
                        response = page.goto(url, wait_until='domcontentloaded', timeout=35000)
                        if response and response.status >= 400: continue
                        try: page.wait_for_selector('script[type="application/ld+json"]', state='attached', timeout=5000)
                        except Exception: pass
                        scripts = page.locator('script[type="application/ld+json"]').all_text_contents()
                        for script in scripts:
                            try: data = json.loads(script)
                            except ValueError: continue
                            for job in postings(data):
                                job['url'] = url
                                parsed_count += 1
                                result = assess(job)
                                if result: found[url] = result
                    except Exception: continue
                if not parsed_count: errors.append(source['name'] + ': описания недоступны или формат сайта изменился')
            except Exception:
                errors.append(source['name'] + ': сбор недоступен (блокировка, сеть или формат сайта)')
        browser.close()
    return list(found.values()), errors

def report(jobs, errors, seen):
    today = dt.datetime.now(ZoneInfo('Europe/Kyiv')).date().isoformat()
    main = [j for j in jobs if 'Локация требует' not in j['location'] and j['score'] >= 3]
    pending = [j for j in jobs if 'Локация требует' in j['location'] and j['score'] >= 3]
    rank = lambda j: (j['url'] not in seen, j['score'], -j['age'])
    main.sort(key=rank, reverse=True)
    pending.sort(key=rank, reverse=True)
    selected = main[:15]
    lines = ['Вакансии аналитика · '+today, 'Бесплатная оценка по правилам, без AI.',
             'Дата из метаданных сайта; первая публикация/обновление не различены.']
    if selected and any(j['age'] > 7 for j in selected): lines.append('Поиск расширен до 14 дней.')
    def card(j, i):
        label = 'повтор из предыдущих отправок' if j['url'] in seen else 'впервые в отправках этого бота'
        fit = 'высокое по правилам' if j['score'] >= 8 and not j['gaps'] else 'среднее по правилам'
        return '\n'.join([f"\n{i}. {j['employer']} — {j['title']}",
            f"{j['date']} · {label} · отрасль: {j['sector']}",
            f"{j['format']} · {j['address']} · {j['location']}",
            'Зарплата: '+j['salary'],
            '; '.join(k+': '+v for k,v in j['skills'].items()),
            'Соответствие: '+fit+'; '+('; '.join(j['gaps']) or 'основной плюс: навыки обработки данных'), j['url']])
    for i,j in enumerate(selected, 1): lines.append(card(j,i))
    if not selected: lines.append('\nПодтверждённых подходящих вакансий в основном топе нет. Это не означает, что на рынке вакансий нет.')
    if pending:
        lines.append('\nЛОКАЦИЯ ТРЕБУЕТ УТОЧНЕНИЯ — вне основного топа')
        for i,j in enumerate(pending[:5],1): lines.append(card(j,i))
    if selected: lines.append('\nПроверить в первую очередь: '+', '.join(j['employer'] for j in selected[:3]))
    lines.append('\nTelegram-каналы, Djinni, Jooble и сайты работодателей: в этой версии не подключены. Национальность компании, метро и время в пути автоматически не подтверждаются.')
    if errors: lines.append('\nПроблемы источников:\n'+'\n'.join(errors))
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
