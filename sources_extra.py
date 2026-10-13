"""Public HTML/RSS readers: DOU, Happy Monday, Jooble primary-source checks."""
import datetime as dt
import json
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

class Node:
    def __init__(self, tag='', attrs=None):
        self.tag, self.attrs, self.children = tag, dict(attrs or []), []
    def text(self):
        if self.tag in ('script','style'): return ''
        return '\n'.join(c.text() if isinstance(c,Node) else c for c in self.children)
    def walk(self):
        yield self
        for c in self.children:
            if isinstance(c,Node): yield from c.walk()

class Tree(HTMLParser):
    VOID={'meta','link','img','input','br','hr','source','area','base','wbr','embed','param','track','col'}
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.root=Node();self.stack=[self.root];self.feed(value)
    def handle_starttag(self,tag,attrs):
        n=Node(tag,attrs);self.stack[-1].children.append(n)
        if tag not in self.VOID:self.stack.append(n)
    def handle_startendtag(self,tag,attrs):
        self.stack[-1].children.append(Node(tag,attrs))
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag==tag:self.stack=self.stack[:i];break
    def handle_data(self,value):self.stack[-1].children.append(value)

def fetch(url):
    with urlopen(Request(url,headers={'User-Agent':'KyivAnalystDigest/1.0 (personal public vacancy reader)'}),timeout=25) as r:
        if len(r.read(0)):pass
        raw=r.read(2500000)
        charset=r.headers.get_content_charset() or 'utf-8'
        return raw.decode(charset,errors='replace'),r.url

def canonical(url):
    p=urlsplit(url)
    return urlunsplit((p.scheme,p.netloc,p.path.rstrip('/'),'',''))

def read_job(markup,url):
    tree=Tree(markup);nodes=list(tree.root.walk())
    h=next((n for n in nodes if n.tag=='h1'),None)
    if h and re.search(r'неактивн|закрит|закрыт|inactive|no longer',h.text(),re.I):return None
    for n in nodes:
        if n.tag=='script' and n.attrs.get('type')=='application/ld+json':
            raw=''.join(c for c in n.children if isinstance(c,str))
            try:data=json.loads(raw)
            except ValueError:continue
            values=data if isinstance(data,list) else data.get('@graph',[data]) if isinstance(data,dict) else []
            for j in values:
                if isinstance(j,dict) and j.get('@type')=='JobPosting':
                    j['url']=canonical(url);return j
    def prop(key):
        n=next((n for n in nodes if n.attrs.get('itemprop')==key),None)
        return (n.attrs.get('content') or n.text()).strip() if n else ''
    if 'happymonday' in url:
        title=prop('title') or (h.text().strip() if h else '')
        org=next((n for n in nodes if n.attrs.get('itemprop')=='hiringOrganization'),None)
        company=next((n.text().strip() for n in org.walk() if n.attrs.get('itemprop')=='name'),'Компания требует проверки') if org else 'Компания требует проверки'
        return {'title':title,'description':prop('description'),'datePosted':prop('datePosted'),
          'validThrough':prop('validThrough'),'hiringOrganization':{'name':company},
          'jobLocation':{'address':{'streetAddress':prop('jobLocation')}},'url':canonical(url)}
    if 'jobs.dou.ua' in url:
        title=h.text().strip() if h else ''
        desc=next((n.text() for n in nodes if 'vacancy-section' in n.attrs.get('class','')), '')
        date=next((n.text().strip() for n in nodes if n.attrs.get('class')=='date'),'')
        place=next((n.text().strip() for n in nodes if 'place' in n.attrs.get('class','').split()),'')
        company=next((n.text().strip() for n in nodes if n.tag=='a' and 'company-name' in n.attrs.get('class','')),'')
        if not company:
            page_title=next((n.text() for n in nodes if n.tag=='title'),'')
            m=re.search(r'\sв\s(.+?),',page_title)
            company=m[1] if m else 'Компания требует проверки'
        months={'січня':1,'лютого':2,'березня':3,'квітня':4,'травня':5,'червня':6,'липня':7,'серпня':8,'вересня':9,'жовтня':10,'листопада':11,'грудня':12}
        m=re.search(r'(\d{1,2})\s+('+'|'.join(months)+r')\s+(20\d{2})',date)
        if not m:return None
        return {'title':title,'description':desc,'datePosted':dt.date(int(m[3]),months[m[2]],int(m[1])).isoformat(),
          'hiringOrganization':{'name':company},'jobLocation':{'address':{'streetAddress':place}},'url':canonical(url)}
    return None

def collect_extra(assess):
    found=[];notes=[]
    today=dt.datetime.now(dt.timezone.utc).date()
    try:
        xml,_=fetch('https://jobs.dou.ua/vacancies/feeds/?city=Kyiv&category=Analyst')
        candidates=[]
        for item in ET.fromstring(xml).findall('./channel/item'):
            title=item.findtext('title','');url=item.findtext('link','');published=item.findtext('pubDate','')
            if not re.search(r'аналітик|аналитик|analyst',title,re.I):continue
            try:
                if (today-parsedate_to_datetime(published).date()).days>14:continue
            except (ValueError,TypeError):continue
            candidates.append(url)
        count=0;failures=0
        for url in candidates[:25]:
            try:
                markup,final=fetch(url);job=read_job(markup,final)
                if job:
                    count+=1;result=assess(job)
                    if result:found.append(result)
            except Exception:failures+=1
        notes.append(f'DOU: ссылок {len(candidates[:25])}, прочитано {count}, ошибок {failures}')
    except Exception as e:notes.append('DOU: недоступен; '+type(e).__name__)
    for name,url,pattern,limit in [
      ('Happy Monday','https://happymonday.ua/jobs-search/data-analyst',r'/jobs/\d+',20),
      ('Jooble','https://ua.jooble.org/SearchResult?ukw=data%20analyst&rgns=%D0%9A%D0%B8%D1%97%D0%B2',r'/jdp/-?\d+',15)]:
        try:
            markup,_=fetch(url);tree=Tree(markup)
            links=list(dict.fromkeys(urljoin(url,n.attrs['href']) for n in tree.root.walk()
              if n.tag=='a' and re.search(pattern,n.attrs.get('href',''))))[:limit]
            count=0;failed=0
            for link in links:
                try:
                    markup,final=fetch(link)
                    if name=='Jooble':
                        away=next((urljoin(final,n.attrs['href']) for n in Tree(markup).root.walk()
                          if n.tag=='a' and '/away/' in n.attrs.get('href','')),None)
                        if not away:failed+=1;continue
                        markup,final=fetch(away)
                        if 'jooble.org' in urlsplit(final).hostname:failed+=1;continue
                    job=read_job(markup,final)
                    if job:
                        count+=1;result=assess(job)
                        if result:found.append(result)
                    else:failed+=1
                except Exception:failed+=1
            notes.append(f'{name}: ссылок {len(links)}, проверено описаний {count}, не подтверждено {failed}')
        except Exception as e:notes.append(name+': недоступен; '+type(e).__name__)
    return found,notes
