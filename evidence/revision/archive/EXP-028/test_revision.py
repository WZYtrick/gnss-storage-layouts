"""Meaningful regression checks for the new adapter and analysis decisions."""
from common import *
import tempfile
import unittest
import math
import numpy as np
from georinex_reader import GeoRinexReader, UnsupportedSemantics
from direct_reader import DirectReader
from evidence_analysis import select,qualify

def make_source(folder,duplicate=False):
    def header(content,label):return content.ljust(60)+label+'\n'
    text=header('     3.04           OBSERVATION DATA    G','RINEX VERSION / TYPE')
    text+=header('G    3 C1C L5Q S1C','SYS / # / OBS TYPES')
    text+=header('','END OF HEADER')
    def cell(value,lli=' ',ssi=' '):return ((' '*14) if value is None else f'{value:14.3f}')+lli+ssi
    def epoch(second,lines):
        return f'> 2025 04 01 00 00 {second:10.7f}  0{len(lines):3d}\n'+''.join(line+'\n' for line in lines)
    g1='G01'+cell(-0.0,'0','0')+cell(123.5,'1',' ')+cell(None,' ','0')
    g2='G02'+cell(None)+cell(None)+cell(None)
    text+=epoch(0,[g1,g2]+([g1] if duplicate else []))
    text+=epoch(30,['G01'+cell(5.0)+cell(6.0)+cell(7.0)])
    path=Path(folder)/'fixture.rnx';path.write_text(text,encoding='ascii')
    return dict(codes=['C1C','L5Q','S1C','D1C'],sources=[dict(id=1,source_file=str(path),events=[],duplicate_epoch_sat_rows=0)])

class RevisionTests(unittest.TestCase):
    def test_full_semantics_and_half_open(self):
        with tempfile.TemporaryDirectory(prefix='exp028_',dir=ROOT/'validation') as folder:
            manifest=make_source(folder)
            base=s.epoch_base('2025-04-01')
            q=dict(source_ids=[1],start=base,end=base+30000000,codes=manifest['codes'],systems=None,satellite=None)
            expected,_=DirectReader(manifest,decimal_values=True).query(q)
            actual,_=GeoRinexReader(manifest).query(q)
            self.assertEqual(s.fingerprint(expected),s.fingerprint(actual))
            self.assertEqual(len(actual['rid']),2)
            self.assertTrue(np.signbit(actual['v'][0,0]))
            self.assertEqual(actual['l'][0,1],1) # L5 flag absent from GeoRinex's native indicator subset.
            self.assertTrue(np.isnan(actual['v'][1]).all())
            self.assertTrue(np.isnan(actual['v'][:,3]).all())
            self.assertEqual(actual['s'][0,2],0)
            q.update(start=base+1,end=base+30000000)
            empty,_=GeoRinexReader(manifest).query(q)
            self.assertEqual(len(empty['rid']),0)
    def test_duplicate_rejected_not_collapsed(self):
        with tempfile.TemporaryDirectory(prefix='exp028_',dir=ROOT/'validation') as folder:
            manifest=make_source(folder,duplicate=True);base=s.epoch_base('2025-04-01')
            q=dict(source_ids=[1],start=base,end=base+1,codes=manifest['codes'],systems=None,satellite=None)
            with self.assertRaises(UnsupportedSemantics):GeoRinexReader(manifest).query(q)
    def test_near_boundary_and_ties(self):
        sizes={'W':dict(total_bytes=100,files=2),'C':dict(total_bytes=90,files=2),'Cstar':dict(total_bytes=90,files=1)}
        self.assertEqual(select({'W':100,'C':103,'Cstar':103},sizes,.03),'Cstar')
        self.assertEqual(select({'W':100,'C':103.00001,'Cstar':103.00001},sizes,.03),'W')
        sizes['C']['files']=1
        self.assertEqual(select({'W':100,'C':100,'Cstar':100},sizes,0),'C')
    def test_window_boundaries(self):
        tz=dt.timezone(dt.timedelta(hours=8))
        self.assertFalse(in_window(dt.datetime(2026,11,13,0,59,59,tzinfo=tz)))
        self.assertTrue(in_window(dt.datetime(2026,11,13,1,0,tzinfo=tz)))
        self.assertTrue(in_window(dt.datetime(2026,11,13,7,59,59,tzinfo=tz)))
        self.assertFalse(in_window(dt.datetime(2026,11,13,8,0,tzinfo=tz)))
    def test_qualification_boundaries(self):
        self.assertEqual(qualify(.95,5,1), (True,False))
        self.assertEqual(qualify(.95,4,1), (False,False))
        self.assertEqual(qualify(.95001,6,1), (False,False))
        self.assertEqual(qualify(1.03,0,.95), (False,True))
        self.assertEqual(qualify(1.03001,0,.95), (False,False))
        self.assertEqual(qualify(1.03,0,.95001), (False,False))
    def test_formal_worker_rejects_wrong_window_and_code(self):
        import campaign
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(prefix='exp028_gate_',dir=ROOT/'validation') as folder:
            root=Path(folder)
            design=dict(runtime_code=campaign.runtime_hashes(),input_files=[],jobs=[dict(id='test',kind='baseline',dataset='D1',block=0)])
            dump(root/'design.json',design)
            with patch.object(campaign,'in_window',return_value=False):
                with self.assertRaisesRegex(RuntimeError,'outside'):campaign.worker(root,'test')
            self.assertFalse((root/'runs').exists())
            design['runtime_code']['reader.py']='0'*64
            with self.assertRaises(AssertionError):campaign.guard(design)

if __name__=='__main__':
    (ROOT/'validation').mkdir(exist_ok=True)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RevisionTests))
    dump(ROOT/'validation/regression_tests.json',dict(utc=utc(),all_passed=result.wasSuccessful(),tests=result.testsRun,
        failures=[str(x) for x in result.failures],errors=[str(x) for x in result.errors],
        runtime_code=__import__('campaign').runtime_hashes()))
    raise SystemExit(0 if result.wasSuccessful() else 1)
