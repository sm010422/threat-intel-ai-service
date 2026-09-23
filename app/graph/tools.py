"""Gemini function-calling 도구 3종. call_with_tools()가 이 중 필요한 것만
모델 스스로 골라 순서대로 호출하는 멀티스텝 플래닝 에이전트를 구성한다
(app/llm/gemini_client.py 참고).
"""

import random

import google.generativeai as genai

# --- 1. assess_threat_level ------------------------------------------------
# 규칙 기반 위협 등급. target-tracking-service(Java)가 LLM 호출 전에 적용하는
# 것과 동일한 룰 -- target-tracking-service/docs/ai-analysis.md의 "규칙 기반
# 위협 등급" 표 참고. 프롬프트에 박아넣지 않고 도구로 노출해서, 질문마다
# 정량 등급이 실제로 필요한지를 모델이 스스로 판단하게 한다.

ASSESS_THREAT_LEVEL_DECLARATION = genai.protos.FunctionDeclaration(
    name="assess_threat_level",
    description=(
        "표적 유형, 고도(m), 속도(km/h)를 입력받아 규칙 기반 위협 등급"
        "(CRITICAL/HIGH/MEDIUM/LOW)을 반환한다. 표적의 위협 수준을 정량적으로 "
        "판단해야 하는 질문에 답할 때 호출한다."
    ),
    parameters=genai.protos.Schema(
        type=genai.protos.Type.OBJECT,
        properties={
            "target_type": genai.protos.Schema(
                type=genai.protos.Type.STRING,
                description="DRONE, MISSILE, AIRCRAFT 중 하나",
            ),
            "altitude": genai.protos.Schema(type=genai.protos.Type.NUMBER, description="고도(m)"),
            "speed": genai.protos.Schema(type=genai.protos.Type.NUMBER, description="속도(km/h)"),
        },
        required=["target_type", "altitude", "speed"],
    ),
)


def assess_threat_level(target_type: str, altitude: float, speed: float) -> str:
    # function_call.args는 protobuf Struct에서 온 값이라 숫자가 문자열로
    # 들어올 수 있다 -- 명시적으로 캐스팅해서 비교 연산이 깨지지 않게 한다.
    target_type = str(target_type).upper()
    altitude = float(altitude)
    speed = float(speed)

    if target_type == "MISSILE":
        return "CRITICAL"
    if target_type == "DRONE" and speed > 250 and altitude < 100:
        return "CRITICAL"
    if target_type == "DRONE" and altitude < 50:
        return "HIGH"
    if target_type == "AIRCRAFT" and speed > 800 and altitude < 500:
        return "HIGH"
    if speed > 200:
        return "MEDIUM"
    return "LOW"


# --- 2. lookup_response_procedure ------------------------------------------
# 위협 등급을 매긴 다음 "그래서 어떻게 대응하나"로 이어지는 두 번째 스텝.
# doc_rag 라우트가 쓰는 것과 동일한 threat_documents 검색(search_documents)을
# 그대로 재사용한다 -- 대응 절차 문서가 이미 벡터DB에 있으니 별도 데이터 소스를
# 새로 만들 필요가 없다.

LOOKUP_RESPONSE_PROCEDURE_DECLARATION = genai.protos.FunctionDeclaration(
    name="lookup_response_procedure",
    description=(
        "위협 패턴/전술 상황을 설명하는 텍스트로 위협 인텔리전스 문서를 검색해, "
        "가장 관련성 높은 대응 절차를 반환한다. 위협 등급을 평가한 뒤 구체적인 "
        "대응 조치가 궁금한 질문에 답할 때 호출한다."
    ),
    parameters=genai.protos.Schema(
        type=genai.protos.Type.OBJECT,
        properties={
            "threat_description": genai.protos.Schema(
                type=genai.protos.Type.STRING,
                description="검색할 위협 패턴/상황 설명 (예: '저고도 고속 자폭 드론 접근')",
            ),
        },
        required=["threat_description"],
    ),
)


async def lookup_response_procedure(threat_description: str) -> str:
    # gemini_client -> tools -> doc_store -> gemini_client 순환 임포트를 피하려고
    # 모듈 최상단이 아니라 호출 시점에 지연 임포트한다.
    from app.rag.doc_store import search_documents

    hits = await search_documents(str(threat_description), top_k=1)
    if not hits:
        return "관련 대응 절차 문서를 찾지 못했습니다."
    return hits[0]["text"]


# --- 3. check_intercept_asset_availability ---------------------------------
# 데모용 시뮬레이션 데이터 -- 실제 자산 관리 시스템과 연동되지 않는다. 이 포트폴리오에
# "요격 자산" 개념 자체가 SITREP 문구("① 즉각 요격" 등)에만 있고 구조화된 데이터가
# 없어서, 위협 지식 베이스(10개 패턴)처럼 정적 시드 데이터로 새로 만들었다.
# READY/COOLDOWN을 매 호출마다 섞는 건 "항상 같은 답만 나오는 가짜 도구"처럼
# 보이지 않게 하기 위함 -- 그래도 본질은 시뮬레이션이라는 점은 변하지 않는다.

CHECK_INTERCEPT_ASSET_DECLARATION = genai.protos.FunctionDeclaration(
    name="check_intercept_asset_availability",
    description=(
        "표적 유형에 대응 가능한 요격 자산의 현재 가용 상태를 조회한다 (데모용 "
        "시뮬레이션 데이터, 실제 자산 관리 시스템과 무관). 실제 대응 조치를 취할 "
        "여력이 있는지 판단해야 하는 질문에 답할 때 호출한다."
    ),
    parameters=genai.protos.Schema(
        type=genai.protos.Type.OBJECT,
        properties={
            "target_type": genai.protos.Schema(
                type=genai.protos.Type.STRING,
                description="DRONE, MISSILE, AIRCRAFT 중 하나",
            ),
        },
        required=["target_type"],
    ),
)

_INTERCEPT_ASSETS = {
    "DRONE": ["근접방어화기(CIWS) 1문대", "대드론 재밍 시스템 2기"],
    "MISSILE": ["패트리어트 포대 1개", "천궁-II 포대 1개"],
    "AIRCRAFT": ["KF-21 대기편대 2대", "지대공 미사일 포대 1개"],
}


def check_intercept_asset_availability(target_type: str) -> str:
    assets = _INTERCEPT_ASSETS.get(str(target_type).upper(), ["가용 자산 정보 없음"])
    statuses = [f"{asset}: {random.choice(['READY', 'READY', 'COOLDOWN'])}" for asset in assets]
    return "(시뮬레이션 데이터) " + "; ".join(statuses)


ALL_TOOLS = genai.protos.Tool(
    function_declarations=[
        ASSESS_THREAT_LEVEL_DECLARATION,
        LOOKUP_RESPONSE_PROCEDURE_DECLARATION,
        CHECK_INTERCEPT_ASSET_DECLARATION,
    ]
)

TOOL_FUNCTIONS = {
    "assess_threat_level": assess_threat_level,
    "lookup_response_procedure": lookup_response_procedure,
    "check_intercept_asset_availability": check_intercept_asset_availability,
}
