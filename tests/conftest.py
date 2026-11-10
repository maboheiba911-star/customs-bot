import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from customs_bot.settings import Settings  # noqa: E402

# Real responses captured from customs.gov.eg/Services/TrfDetails on 2026-11-10
OFFICIAL_SAMPLES = {
    "0713330090": {
        "Number": "07/13/33/00/90",
        "ShortDesc": "فاصولياء عادية  [Phaseolus vulgaris]  ، بما فيها الفاصولياء...",
        "Desc": "فاصولياء عادية  [Phaseolus vulgaris]  ، بما فيها الفاصولياء البيضاء ،  يابسة  ، مقشورة  لغير البذار ،  وإن كانت منزوعة الغلالة أو مفلقة أو مكسرة .",
        "Taxes": ["ضريبة الوارد :  2%", "ضريبة قيمه مضافه :  صفر"],
        "InstructionCodes": ["غ6502", "غ6508"],
        "Instructions": [
            "غ6502-يستثنى من الاعفاء أو التخفيض الصادرات المصرية من المنتجات الزراعية والمصنعة والسمكية",
            "غ6508-فى اطار اتفاقية منطقة حرة بين ج.م.ع دول افتا تخضع صادرات مصريةمن أسماك وسلع زراعيةلا",
        ],
    },
    "8465950000": {"Number": "84/65/95/00/00", "Taxes": ["ضريبة الوارد :  صفر", ""]},
    "2402200000": {"Number": "24/02/20/00/00", "Taxes": [
        "ضريبة الوارد :  100 جنية لكل كيلو جرام صافى",
        "تامين صحى وزارة الصحة :  0.1 جنية لكل عشرون سيجارة", ""]},
    "8703233090": {"Number": "87/03/23/30/90", "Taxes": [
        "ضريبة الوارد :  40%", "", "ضريبة الجدول :  1% (من القيمة + ر.ض.جمركية)"]},
    "6404110000": {"Number": "64/04/11/00/00", "Taxes": [
        "ضريبة الوارد :  60%", "ضريبة قيمه مضافه :  14% (من القيمة + ر.ض.جمركية)"]},
    "1905320000": {"Number": "19/05/32/00/00", "Taxes": [
        "ضريبة الوارد :  60%", "ضريبة قيمه مضافه :  5% (من القيمة + ر.ض.جمركية)",
        "اتفاقيه الشراكه المصريه الاوربيه", "ضريبة الوارد :  15%",
        "اتفاقيه تركيا", "ضريبة الوارد :  25.5%", "اتفاقيه بريطانيا", "ضريبة الوارد :  15%"]},
    "9018391035": {"Number": "90/18/39/10/35", "Taxes": ["ضريبة الوارد :  صفر", "حره افريقيه ب", "ضريبة الوارد :  2%"]},
}


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.token = "123456:TEST"
    s.admin_ids = {1001}
    s.data_dir = tmp_path
    s.reference_db = tmp_path / "reference.db"
    s.app_db = tmp_path / "app.db"
    s.live_lookup = False
    shutil.copy2(ROOT / "data" / "reference.db", s.reference_db)
    return s
