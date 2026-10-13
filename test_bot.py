import datetime as dt
import unittest
from zoneinfo import ZoneInfo
from bot import assess, postings, report

def job(address='Київ, Кирилівська 74', desc='Excel Power Query фарма'):
    return {'title':'Аналітик консолідованої інформації', 'description':desc,
      'url':'https://example.org/job/1', 'datePosted':dt.datetime.now(ZoneInfo('Europe/Kyiv')).date().isoformat(),
      'hiringOrganization':{'name':'Приклад'}, 'jobLocation':{'address':{'streetAddress':address}}}

class Filters(unittest.TestCase):
    def test_left_bank_excluded(self):
        self.assertIsNone(assess(job('Київ, Чавдар 5')))
    def test_hybrid_left_bank_excluded(self):
        self.assertIsNone(assess(job('Київ, Лівобережна', 'Excel гібрид')))
    def test_unknown_address_not_main(self):
        j=assess(job('Київ'))
        _, selected=report([j], [], {})
        self.assertEqual(j['location'], 'Локация требует уточнения')
        self.assertIn('ЛОКАЦИЯ ТРЕБУЕТ', report([j], [], {})[0])
    def test_advanced_tools_penalty(self):
        a=assess(job())
        b=assess(job(desc='Excel Power Query фарма. SQL на високому рівні'))
        self.assertLess(b['score'],a['score'])
    def test_undated_not_fresh(self):
        j=job();j['datePosted']=''
        self.assertIsNone(assess(j))
    def test_old_excluded(self):
        j=job();j['datePosted']='2020-01-01'
        self.assertIsNone(assess(j))
    def test_graph(self):
        self.assertEqual(len(list(postings({'@graph':[{'@type':'JobPosting'}]}))),1)
    def test_repeat_label(self):
        j=assess(job())
        self.assertIn('повтор из предыдущих',report([j],[],{j['url']:'2026-01-01'})[0])
if __name__ == '__main__': unittest.main()
