import unittest
import os
import sys

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.services.csv_collector import parse_plids_from_content

class TestCsvCollector(unittest.TestCase):
    def test_parse_csv_header(self):
        sample_csv = """PLID,出单次数,总出单件数,累计GMV(ZAR),代表性标题,代表性SKU,代表性TSIN,首次出单时间,最近出单时间,当前在售状态
100779861,5,5,3269.0,Solar Charge Controller,HG91A9384690,102455384,2026-09-16,2026-09-17,buyable
95697335,2,5,2977.0,Suspenders,9902544738856,96647213,2026-09-15,2026-09-17,buyable
"""
        items = parse_plids_from_content(sample_csv)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["plid"], "100779861")
        self.assertEqual(items[0]["tsin"], "102455384")
        self.assertEqual(items[1]["plid"], "95697335")
        self.assertEqual(items[1]["tsin"], "96647213")

    def test_parse_plain_text(self):
        sample_txt = """PLID100779861
https://www.takealot.com/x/PLID95697335
101256563
PLID100779861
"""
        items = parse_plids_from_content(sample_txt)
        # Should deduplicate PLID100779861
        self.assertEqual(len(items), 3)
        self.assertEqual([i["plid"] for i in items], ["100779861", "95697335", "101256563"])

    def test_parse_bytes_with_bom(self):
        raw_bytes = "\ufeffPLID,代表性TSIN\n100779861,102455384\n".encode("utf-8")
        items = parse_plids_from_content(raw_bytes)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["plid"], "100779861")

    def test_import_csv_api(self):
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        
        # Test JSON body with plids
        resp = client.post("/api/products/import-csv", json={
            "plids": ["100779861", "95697335"],
            "skip_existing": True
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("total_plids"), 2)
        self.assertIn("task_id", data)

if __name__ == "__main__":
    unittest.main()
