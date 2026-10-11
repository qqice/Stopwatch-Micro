import collections
import unittest
from serial_debug_test import SerialLineReader

class Input:
 def __init__(self,chunks):self.chunks=collections.deque(chunks);self.requests=[]
 def read_until(self,expected,size):self.requests.append(size);return self.chunks.popleft() if self.chunks else b''
 def close(self):self.closed=True

class LineReaderTests(unittest.TestCase):
 def test_packet_split_preserves_ack_and_ms(self):
  s=Input([b'DBG RX_PACKET ms=10 ack=2939',b'',b'583136 flags=16\r\n'])
  r=SerialLineReader(s)
  self.assertEqual(r.readline(),b'');self.assertEqual(r.readline(),b'')
  self.assertEqual(r.readline(),b'DBG RX_PACKET ms=10 ack=2939583136 flags=16\r\n')
  self.assertEqual(r.partial_reads,1);self.assertEqual(r.buffer,b'');self.assertEqual(r.dropped_lines,0)
 def test_oversize_tail_never_becomes_result(self):
  s=Input([b'xxxxxxxxx',b'DBG RESULT command=ping status=PASS\n',b'OK\n'])
  r=SerialLineReader(s,8)
  self.assertEqual(r.readline(),b'');self.assertEqual(r.readline(),b'')
  self.assertEqual(r.readline(),b'OK\n');self.assertEqual(r.dropped_lines,1)
 def test_repeated_fragment_overflow_and_timeout(self):
  s=Input([b'1234',b'5678',b'X',b'',b'END\n',b'YES\n']);r=SerialLineReader(s,8)
  for _ in range(5):self.assertEqual(r.readline(),b'')
  self.assertEqual(r.readline(),b'YES\n');self.assertEqual(r.dropped_lines,1)
  self.assertTrue(all(n==9 for n in s.requests));self.assertLessEqual(len(r.buffer),8)
 def test_crlf_and_api_delegation(self):
  s=Input([b'OK\r',b'\n']);r=SerialLineReader(s)
  self.assertEqual(r.readline(),b'');self.assertEqual(r.readline(),b'OK\r\n')
  r.close();self.assertTrue(s.closed)
 def test_serial_property_writes_reach_real_device(self):
  s=Input([]);s.baudrate=115200;s.rts=False;s.dtr=False;s.timeout=0.1
  r=SerialLineReader(s);r.baudrate=1200;r.rts=True;r.dtr=True;r.timeout=0.25
  self.assertEqual(s.baudrate,1200);self.assertTrue(s.rts);self.assertTrue(s.dtr)
  self.assertEqual(s.timeout,0.25);self.assertEqual(r.baudrate,1200)
  self.assertNotIn('baudrate',r.__dict__)
 def test_flush_discards_buffered_prefix(self):
  s=Input([b'PARTIAL']);s.reset_input_buffer=lambda:None;r=SerialLineReader(s)
  self.assertEqual(r.readline(),b'');self.assertTrue(r.buffer)
  r.reset_input_buffer();self.assertFalse(r.buffer);self.assertFalse(r.discarding)
 def test_bad_limits(self):
  for n in (0,-1,65537):
   with self.assertRaises(ValueError):SerialLineReader(Input([]),n)

if __name__=='__main__':unittest.main()
