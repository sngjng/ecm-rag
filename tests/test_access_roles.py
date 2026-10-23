"""사용자 권한 기본값과 명칭 변경 회귀 테스트."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from service.repositories.access_roles import resolve_identity_role
from service.schemas import AccessRoleUpsert, EmployeeIdentity


class AccessRoleTests(unittest.TestCase):
    def test_unregistered_employee_defaults_to_uploader(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = None
        context = MagicMock()
        context.__enter__.return_value = connection
        context.__exit__.return_value = False
        identity = EmployeeIdentity(
            employee_id="E10001",
            employee_name="테스트 사원",
        )

        with (
            patch("service.repositories.access_roles._bootstrap_admin_ids", return_value=set()),
            patch("service.repositories.access_roles.connect", return_value=context),
        ):
            resolved = resolve_identity_role(identity)

        self.assertEqual(resolved.roles, ["uploader"])

    def test_viewer_is_valid_role_name(self):
        payload = AccessRoleUpsert(role="viewer")
        self.assertEqual(payload.role, "viewer")


if __name__ == "__main__":
    unittest.main()
