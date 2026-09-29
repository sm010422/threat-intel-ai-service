"""app/graph/tools.py의 순수 함수 3종을 검증한다. Gemini API 호출이 전혀
없는 로직이라 네트워크/API 키 없이도 CI에서 그대로 돌아간다.
"""

import pytest

from app.graph.tools import assess_threat_level, check_intercept_asset_availability


class TestAssessThreatLevel:
    """target-tracking-service(Java)와 동일한 규칙 기반 위협 등급 표를 검증한다."""

    @pytest.mark.parametrize(
        "target_type,altitude,speed,expected",
        [
            ("MISSILE", 500, 300, "CRITICAL"),
            ("missile", 10, 50, "CRITICAL"),  # 대소문자 무관
            ("DRONE", 80, 280, "CRITICAL"),  # 속도>250 & 고도<100
            ("DRONE", 30, 100, "HIGH"),  # 고도<50
            ("AIRCRAFT", 400, 850, "HIGH"),  # 속도>800 & 고도<500
            ("AIRCRAFT", 5000, 250, "MEDIUM"),  # 속도>200
            ("AIRCRAFT", 5000, 100, "LOW"),
            ("SHIP", 0, 80, "MEDIUM"),  # 상선 순항 속도(30km/h대) 대비 이례적 고속
            ("SHIP", 0, 20, "LOW"),
        ],
    )
    def test_rule_table(self, target_type, altitude, speed, expected):
        assert assess_threat_level(target_type, altitude, speed) == expected

    def test_accepts_string_numbers_from_function_call_args(self):
        # Gemini function_call.args는 protobuf Struct에서 온 값이라 숫자가
        # 문자열로 들어올 수 있다 -- 캐스팅이 실제로 동작하는지 확인.
        assert assess_threat_level("DRONE", "30", "300") == "CRITICAL"


class TestCheckInterceptAssetAvailability:
    """2026-09-29: 랜덤 READY/COOLDOWN 시뮬레이션을 하버사인 거리 기반 실제 ETA
    계산으로 교체했다 -- target-tracking-service의 AssetRecommendationService와
    동일한 카탈로그/공식을 쓰는지 검증한다."""

    @pytest.mark.parametrize("target_type", ["DRONE", "MISSILE", "AIRCRAFT"])
    def test_known_target_type_returns_ranked_assets_with_eta(self, target_type):
        result = check_intercept_asset_availability(target_type)
        assert "ETA" in result
        assert "km" in result

    def test_unknown_target_type_falls_back_gracefully(self):
        result = check_intercept_asset_availability("SUBMARINE")
        assert "대응 가능한 자산이 카탈로그에 없습니다" in result

    def test_seoul_area_target_prefers_nearby_asset(self):
        result = check_intercept_asset_availability("DRONE", target_latitude=37.5665, target_longitude=126.9780)
        assert result.startswith("K30 비호 대공포(수도권)")

    def test_defaults_to_seoul_when_coordinates_omitted(self):
        with_coords = check_intercept_asset_availability("DRONE", target_latitude=37.5665, target_longitude=126.9780)
        without_coords = check_intercept_asset_availability("DRONE")
        assert with_coords == without_coords

    def test_far_away_target_can_be_marked_infeasible(self):
        # 우크라이나 키이우 권역 -- 국내 자산의 연료/반응시간 예산을 훨씬 초과
        result = check_intercept_asset_availability("DRONE", target_latitude=50.4501, target_longitude=30.5234)
        assert "예산 초과" in result
