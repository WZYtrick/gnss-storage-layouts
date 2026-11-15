"""Synthetic fault-injection checks; these fixtures are NEVER experimental evidence."""
from common import *
import copy
import math
import unittest
from unittest.mock import patch
import analyze_revision as primary
import independent_revision as independent

class AnalysisTests(unittest.TestCase):
    def test_amortization_and_window_boundaries(self):
        self.assertEqual(primary.break_even(10,3,1),5)
        self.assertEqual(primary.break_even(10.01,3,1),6)
        self.assertIsNone(primary.break_even(10,1,1))
        self.assertIsNone(primary.break_even(10,1,2))
        for stamp,expected in [('2026-11-12T16:59:59+00:00',False),('2026-11-12T17:00:00+00:00',True),
                               ('2026-11-12T23:59:59+00:00',True),('2026-11-13T00:00:00+00:00',False)]:
            self.assertEqual(primary.authorized(stamp),expected)

    def test_complete_matrix_and_corrupted_evidence(self):
        root=ROOT/'validation'/('synthetic_analysis_'+utc().replace(':','').replace('+','_'))
        root.mkdir(parents=True)
        (root/'NOT_EXPERIMENTAL_DATA.txt').write_text('Synthetic software test fixture only. Never publish as measured data.\n')
        campaign=root/'campaign';campaign.mkdir()
        code={'fixture':'synthetic'};jobs=[]
        for b in range(6):
            for ds in ['D0m','D1','D2','D2m']:
                job=dict(id=f'build_{ds}_{b}',kind='construction',dataset=ds,block=b);jobs.append(job)
                results=[]
                for method in load(ROOT/'inputs'/ds/'layouts.json'):
                    prep=1+b*.125
                    results.append(dict(dataset=ds,block=b,method=method,decompress_seconds=.5,parse_seconds=prep,
                          build_seconds=2,init_seconds=.125,from_rnx_seconds=prep+2.125,from_crx_seconds=prep+2.625,
                          verified_rows=1,full_source_hashes=[dict(rows=1)],files=1,rows=1))
                dump(campaign/'runs'/job['id']/'verification.json',dict(all_passed=True,purpose='formal',code=code,
                     dataset=ds,block=b,time_policy='Beijing_01_08',ended_utc='2026-11-12T18:00:00+00:00',results=results))
            for ds in ['D1','D2']:
                job=dict(id=f'baseline_{ds}_{b}',kind='baseline',dataset=ds,block=b);jobs.append(job)
                folder=campaign/'runs'/job['id'];folder.mkdir(parents=True)
                _,qs,truth,_=inputs(ds);identity=dict(pid=999999,created=0,executable='SYNTHETIC_FIXTURE')
                dump(folder/'environment.json',dict(process=identity,cpu_pool=1,io_pool=4))
                calls=0
                with (folder/'timings.jsonl').open('w',encoding='utf-8') as f:
                    for repeat in [-1,0,1,2]:
                        for q in qs:
                            for mi,method in enumerate(METHODS):
                                # Round 2, not round 1, is the median. Phase
                                # fractions vary so independently taking phase
                                # medians would fail the returned-stage check.
                                elapsed=(2+mi)*(1+b/16)*[1.5,2,1,1.25][repeat+1]/1000
                                r=dict(dataset=ds,block=b,query=q['id'],family=q['family'],method=method,repeat=repeat,
                                       warmup=repeat==-1,contract='ordered_dense',cpu=1,io=4,process=identity,call_order=calls,
                                       started_utc='2026-11-12T18:00:00+00:00',ended_utc='2026-11-12T18:00:00+00:00',
                                       time_policy='Beijing_01_08',seconds=elapsed,rows=truth[q['id']]['rows'],sha256=truth[q['id']]['sha256'])
                                fraction=[.2,.25,.9,.5][repeat+1]
                                r.update(scan_seconds=elapsed*fraction,normalize_seconds=elapsed*(1-fraction))
                                if method not in CORE:
                                    r.update(decompress_seconds=0,parser_seconds=r['scan_seconds'],adapter_seconds=r['normalize_seconds'],overhead_seconds=0)
                                f.write(json.dumps(r)+'\n');calls+=1
                dump(folder/'verification.json',dict(all_passed=True,formal_calls=4536,warmup_calls=1512,code=code))
        design=dict(jobs=jobs,runtime_code=code,expected=dict(formal_query_calls=54432,warmup_query_calls=18144,layout_builds=204))
        dump(campaign/'design.json',design);dump(campaign/'status.json',dict(state='running'))
        with self.assertRaisesRegex(AssertionError,'complete'):primary.analyze(campaign,root/'must_not_exist')
        self.assertFalse((root/'must_not_exist').exists())
        dump(campaign/'status.json',dict(state='complete'))
        analysis=root/'analysis';primary.analyze(campaign,analysis);independent.verify(campaign,analysis)
        medians=independent.csvrows(analysis/'request_medians.csv')
        self.assertEqual({r['repeat'] for r in medians},{'2'})
        file=analysis/'amortization_summary.csv';original=file.read_text(encoding='utf-8')
        rows=independent.csvrows(file);rows[0]['break_even_queries']=str(int(rows[0]['break_even_queries'])+1)
        primary.csvout(file,rows)
        # Refreshing the primary hash still cannot hide a numerical corruption
        # from the independent formula implementation.
        cert=load(analysis/'verification.json');cert['output_hashes'][file.name]=sha(file);dump(analysis/'verification.json',cert)
        with self.assertRaises(AssertionError):independent.verify(campaign,analysis)
        file.write_text(original,encoding='utf-8',newline='');cert['output_hashes'][file.name]=sha(file);dump(analysis/'verification.json',cert)
        independent.verify(campaign,analysis)
        first=campaign/'runs/baseline_D1_0/timings.jsonl'
        content=first.read_text(encoding='utf-8')
        changed=content.replace('2026-11-12T18:00:00+00:00','2026-11-13T00:00:00+00:00',2)
        first.write_text(changed,encoding='utf-8')
        with self.assertRaises(AssertionError):primary.analyze(campaign,root/'wrong_window')
        first.write_text(content,encoding='utf-8')
        dump(root/'test_result.json',dict(all_passed=True,purpose='synthetic_fault_injection_only'))

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(AnalysisTests))
    dump(ROOT/'validation/analysis_regression_tests.json',dict(all_passed=result.wasSuccessful(),tests=result.testsRun,utc=utc(),
        failures=[str(x) for x in result.failures],errors=[str(x) for x in result.errors]))
    raise SystemExit(0 if result.wasSuccessful() else 1)
