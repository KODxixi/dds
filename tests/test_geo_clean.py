"""
T1 坐标清洗测试
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from geo_clean import clean_coords, city_center


class TestGeoClean:
    """坐标清洗单元测试"""

    def test_zero_coords_invalid(self):
        """(0, 0) 应被标记为 False"""
        df = pd.DataFrame({"lat": [0.0], "lng": [0.0]})
        result = clean_coords(df, "三亚")
        assert result["geo_valid"].iloc[0] == False

    def test_sanya_valid_coords(self):
        """三亚有效坐标 (18.41, 109.71) 应为 True"""
        df = pd.DataFrame({"lat": [18.41], "lng": [109.71]})
        result = clean_coords(df, "三亚")
        assert result["geo_valid"].iloc[0] == True

    def test_sanya_out_of_bbox(self):
        """超出中国范围坐标 (55.0, 136.0) 应为 False"""
        df = pd.DataFrame({"lat": [55.0], "lng": [136.0]})
        result = clean_coords(df, "三亚")
        assert result["geo_valid"].iloc[0] == False

    def test_nan_coords_invalid(self):
        """NaN 坐标应被标记为 False"""
        df = pd.DataFrame({"lat": [float("nan")], "lng": [109.71]})
        result = clean_coords(df, "三亚")
        assert result["geo_valid"].iloc[0] == False

    def test_city_center_calculation(self):
        """city_center 应返回中位数坐标"""
        df = pd.DataFrame({
            "lat": [18.0, 18.5, 19.0],
            "lng": [109.0, 109.5, 110.0]
        })
        center = city_center(df)
        assert center is not None
        assert center[0] == 18.5
        assert center[1] == 109.5

    def test_city_center_with_invalid_points(self):
        """city_center 仅用有效点计算（0 越界，50 有效）"""
        df = pd.DataFrame({
            "lat": [0.0, 18.5, 19.0, 50.0],  # 0 越界（lat < 3.0），其余有效
            "lng": [0.0, 109.5, 110.0, 135.0]
        })
        center = city_center(df)
        assert center is not None
        # 有效点 lat=[18.5, 19.0, 50.0]，中位数 19.0
        assert abs(center[0] - 19.0) < 0.01

    def test_non_destructive(self):
        """clean_coords 应返回新 DataFrame，不改源"""
        df_orig = pd.DataFrame({"lat": [18.41], "lng": [109.71]})
        df_clean = clean_coords(df_orig, "三亚")
        assert "geo_valid" not in df_orig.columns
        assert "geo_valid" in df_clean.columns
        # 原数据列应保留
        assert "lat" in df_clean.columns and "lng" in df_clean.columns

    def test_mixed_validity(self):
        """混合有效和无效坐标"""
        df = pd.DataFrame({
            "lat": [18.41, 0.0, 21.75, 18.5],
            "lng": [109.71, 0.0, 112.0, 109.5]
        })
        result = clean_coords(df, "三亚")
        expected = [True, False, False, True]
        assert result["geo_valid"].tolist() == expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
