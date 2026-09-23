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
        ],
    )
    def test_rule_table(self, target_type, altitude, speed, expected):
        assert assess_threat_level(target_type, altitude, speed) == expected

    def test_accepts_string_numbers_from_function_call_args(self):
        # Gemini function_call.args는 protobuf Struct에서 온 값이라 숫자가
        # 문자열로 들어올 수 있다 -- 캐스팅이 실제로 동작하는지 확인.
        assert assess_threat_level("DRONE", "30", "300") == "CRITICAL"


class TestCheckInterceptAssetAvailability:
    """실제 자산 관리 시스템과 무관한 시뮬레이션 데이터임을 명시하는 것도 검증한다."""

    @pytest.mark.parametrize("target_type", ["DRONE", "MISSILE", "AIRCRAFT"])
    def test_known_target_type_returns_assets_with_status(self, target_type):
        result = check_intercept_asset_availability(target_type)
        assert result.startswith("(시뮬레이션 데이터)")
        assert "READY" in result or "COOLDOWN" in result

    def test_unknown_target_type_falls_back_gracefully(self):
        result = check_intercept_asset_availability("SUBMARINE")
        assert "가용 자산 정보 없음" in result
