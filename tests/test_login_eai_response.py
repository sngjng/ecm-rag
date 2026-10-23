"""실제 사내 EAI에서 확인된 정상 응답을 사용한 로그인 매핑 회귀 테스트.

사내망과 Java 라이브러리가 없는 개발 환경에서도 Java 호출 결과를 모의해 세션 및
브라우저 공개 응답까지 검증한다. 실제 비밀번호나 운영 시스템 호출은 사용하지 않는다.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from service.login import EaiLoginResult, authenticate_employee
from service.routes.auth import _public_employee


# 2026-10-23 사내 EAI 실호출에서 확인된 ZZUL0002VO 값을 테스트 fixture로 고정한다.
NORMAL_EAI_RESPONSE = EaiLoginResult(
    transaction_error_code="00000000",
    return_type="OK",
    return_code="UZZ16001",
    return_message="로그인 되었습니다.",
    user_id="IB1020",
    employee_number="IB1020",
    user_name="오최나",
    department_name="아키텍쳐팀",
    upper_department_name="ACS센터",
)


class EaiLoginResponseTests(unittest.TestCase):
    @patch("service.login._execute_eai_login", return_value=NORMAL_EAI_RESPONSE)
    def test_provided_response_maps_to_login_identity(self, execute_login):
        identity = authenticate_employee("IB1020", "not-a-real-password", "192.168.1.50")

        execute_login.assert_called_once_with(
            "IB1020", "not-a-real-password", "192.168.1.50"
        )
        self.assertEqual(identity.employee_id, "IB1020")
        self.assertEqual(identity.user_id, "IB1020")
        self.assertEqual(identity.employee_name, "오최나")
        self.assertEqual(identity.department, "아키텍쳐팀")
        self.assertEqual(identity.upper_department, "ACS센터")
        self.assertEqual(identity.roles, ["uploader"])

    @patch("service.routes.auth.get_settings")
    def test_identity_is_exposed_as_expected_login_response(self, settings):
        settings.return_value = SimpleNamespace(
            security=SimpleNamespace(
                authentication=SimpleNamespace(enabled=True)
            )
        )
        session = {
            "employee_id": "IB1020",
            "user_id": "IB1020",
            "employee_name": "오최나",
            "department": "아키텍쳐팀",
            "upper_department": "ACS센터",
            "email": "",
            "roles": ["uploader"],
        }

        self.assertEqual(
            _public_employee(session),
            {
                "employee_id": "IB1020",
                "user_id": "IB1020",
                "employee_name": "오최나",
                "department": "아키텍쳐팀",
                "upper_department": "ACS센터",
                "email": "",
                "roles": ["uploader"],
                "authentication_enabled": True,
                "temporary_access": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
