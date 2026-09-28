import unittest
import sys
import os

# Add backend directory to sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.services.ai_cleaner_service import (
    sanitize_accessory_core_name,
    reconstruct_accessory_title,
    extract_device_model
)

class TestAccessoryTitleSanitizer(unittest.TestCase):
    def test_airtag_dog_collar(self):
        title = "Beishi Heavy Duty Dog Collar with AirTag Holder Compatible with Apple AirTag (Orange, 45 - 66 cm)"
        raw = "Heavy Duty Dog Collar with Airtag Holder Case for Small Medium Dogs - Orange"
        res = reconstruct_accessory_title(title, raw, vertical="pet_collar_harness")
        self.assertEqual(res, "Beishi Heavy Duty Dog Collar with Tracker Holder Compatible with Apple AirTag (Orange, 45 - 66 cm)")
        self.assertNotIn("AirTag Holder", res.split("Compatible with")[0])

    def test_ps5_slim_mounting_bracket(self):
        title = "Beishi PS5 Slim Console Mounting Bracket Compatible with PlayStation 5 Slim (White)"
        raw = "PG-TECH Playstation 5 Slim Mounting bracket - White"
        res = reconstruct_accessory_title(title, raw, vertical="docking_station")
        self.assertEqual(res, "Beishi Console Mounting Bracket Compatible with PlayStation 5 Slim (White)")
        self.assertNotIn("PS5", res.split("Compatible with")[0])

    def test_ps5_controller_storage_case(self):
        title = "Beishi PS5 Controller Storage Case Compatible with Sony PS5 Gamepad (White)"
        raw = "For Sony PS5 Controller Nylon Storage Bag Gamepad Carrying Case Protective - White"
        res = reconstruct_accessory_title(title, raw, vertical="cases_covers")
        self.assertEqual(res, "Beishi Controller Storage Case Compatible with Sony PS5 Gamepad (White)")
        self.assertNotIn("PS5", res.split("Compatible with")[0])

    def test_ps5_vertical_stand(self):
        title = "Beishi PS5 Vertical Stand Compatible with PS5 and PS5 Slim Console (Black)"
        raw = "PS5 Vertical Stand with Cooling Fan"
        res = reconstruct_accessory_title(title, raw, vertical="cases_covers")
        self.assertEqual(res, "Beishi Vertical Stand Compatible with PS5 and PS5 Slim Console (Black)")
        self.assertNotIn("PS5", res.split("Compatible with")[0])

    def test_ps5_slim_stand_with_usb(self):
        title = "Beishi PS5 Slim Console Stand with 4-Port USB Hub Compatible with PS5 Slim (Black, 30)"
        raw = "Stand with USB Hub for PS5 Slim"
        res = reconstruct_accessory_title(title, raw, vertical="cases_covers")
        self.assertEqual(res, "Beishi Console Stand with 4-Port USB Hub Compatible with PS5 Slim (Black, 30)")
        self.assertNotIn("PS5", res.split("Compatible with")[0])

    def test_dyson_filter(self):
        title = "Beishi Dyson Filter Compatible with Dyson V11"
        raw = "Dyson V11 Replacement Filter"
        res = reconstruct_accessory_title(title, raw, vertical="vacuum_filter")
        self.assertEqual(res, "Beishi Vacuum Cleaner Filter Compatible with Dyson V11")
        self.assertNotIn("Dyson", res.split("Compatible with")[0])

    def test_apple_watch_band(self):
        title = "Beishi Apple Watch Band Compatible with Apple Watch Series 9"
        raw = "Silicone Band for Apple Watch Series 9"
        res = reconstruct_accessory_title(title, raw, vertical="watch_band")
        self.assertEqual(res, "Beishi Smartwatch Band Compatible with Apple Watch Series 9")
        self.assertNotIn("Apple", res.split("Compatible with")[0])

    def test_magsafe_wallet(self):
        title = "Beishi MagSafe Wallet Compatible with iPhone 15"
        raw = "Magnetic Card Holder for iPhone 15"
        res = reconstruct_accessory_title(title, raw, vertical="cases_covers")
        self.assertEqual(res, "Beishi Magnetic Wallet Compatible with iPhone 15")
        self.assertNotIn("MagSafe", res.split("Compatible with")[0])

    def test_non_accessory_unaffected(self):
        title = "Beishi Men Cotton Casual Crew Neck T-Shirt (Blue, L)"
        raw = "Men Cotton Casual Crew Neck T-Shirt"
        res = reconstruct_accessory_title(title, raw, vertical="t_shirt")
        self.assertEqual(res, title)

if __name__ == "__main__":
    unittest.main()
