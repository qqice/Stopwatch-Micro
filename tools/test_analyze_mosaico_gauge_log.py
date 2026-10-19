import unittest
from analyze_mosaico_gauge_log import analyze,delta16,REQUIRED

def row(ms,raw,rm=62,boot=1,current=-27):
    return dict(type='gauge',page_boot=boot,monotonic_ms=ms,gauge=dict(valid_mask=REQUIRED|(1<<12),
        current_ma=current,fcc_mah=65,raw_cc=raw,remaining_mah=rm,soc=96,voltage_mv=3700,mode=3))

class GaugeAnalysisTests(unittest.TestCase):
    def test_plateau_and_normal_counting(self):
        rows=[row(i*60000,65530+i) for i in range(12)]
        for r in rows:r['gauge']['raw_cc']&=65535
        s=analyze(rows)['segments'][0];self.assertTrue(s['plateau_observed']);self.assertEqual(s['raw_cc_delta_mah'],11)
        for i,r in enumerate(rows):r['gauge']['remaining_mah']=62-i
        s=analyze(rows)['segments'][0];self.assertFalse(s['plateau_observed']);self.assertEqual(s['rm_plus_raw_change_mah'],0)
        self.assertFalse(s['physical_calibration_proven'])
    def test_boot_gap_charge_and_invalid_boundaries(self):
        self.assertEqual(analyze([row(0,1),row(60000,2,boot=2)])['segments'],[])
        self.assertEqual(analyze([row(0,1),row(60000,2,current=69)])['segments'],[])
        self.assertEqual(analyze([row(0,1),row(300000,2)])['segments'],[])
        self.assertEqual(analyze([row(0,1),row(60000,1000)])['segments'],[])
        r=row(60000,2);r['gauge']['valid_mask']=0
        self.assertEqual(analyze([row(0,1),r])['segments'],[])
    def test_wrap(self):
        self.assertEqual(delta16(65534,1),3);self.assertEqual(delta16(1,65534),-3)

if __name__=='__main__':unittest.main()
