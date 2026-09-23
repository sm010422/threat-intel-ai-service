"""classify_question()의 키워드 폴백 경로를 검증한다. GEMINI_API_KEY가 없을 때
(settings.ai_enabled=False) /chat이 여전히 동작해야 한다는 Graceful Degradation
계약(app/config.py, README) 중 라우팅 부분만 떼어서 테스트한다 -- 실제 Gemini
분류 프롬프트 경로는 API 키가 있어야 해서 이 테스트 범위 밖이다.
"""

from app.config import settings
from app.llm.gemini_client import classify_question


class TestClassifyQuestionKeywordFallback:
    def setup_method(self):
        # ai_enabled=False를 강제해서 키워드 폴백 분기만 타게 한다.
        self._original_key = settings.gemini_api_key
        settings.gemini_api_key = ""

    def teardown_method(self):
        settings.gemini_api_key = self._original_key

    def test_history_keyword_routes_to_pattern_search(self):
        assert classify_question("과거에 탐지된 DRONE-001 이력이 있었나?") == "pattern_search"

    def test_similar_target_keyword_routes_to_pattern_search(self):
        assert classify_question("유사한 표적이 최근에 있었나?") == "pattern_search"

    def test_doctrine_question_routes_to_doc_rag(self):
        assert classify_question("저고도 자폭 드론 대응 절차가 뭐야?") == "doc_rag"
