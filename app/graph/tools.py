"""Rule-based threat grading, exposed as a Gemini function-calling tool.

Mirrors the same rule engine target-tracking-service (Java) applies before
the LLM step -- see target-tracking-service/docs/ai-analysis.md's "규칙 기반
위협 등급" table. The point of exposing it as a *tool* rather than baking it
into the prompt is that the model decides per-question whether a quantitative
grade is actually relevant, instead of it being force-fed into every answer.
"""

import google.generativeai as genai

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

THREAT_ASSESSMENT_TOOL = genai.protos.Tool(function_declarations=[ASSESS_THREAT_LEVEL_DECLARATION])


def assess_threat_level(target_type: str, altitude: float, speed: float) -> str:
    target_type = target_type.upper()
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
