"""pooled connection에서 session advisory lock이 남지 않는지 검증한다."""
from service.repositories import assets


class FakeResult:
    def fetchone(self):
        return {"acquired": True}


class FakeConnection:
    def __init__(self):
        self.statements = []

    def execute(self, statement, parameters):
        self.statements.append((statement, parameters))
        return FakeResult()


class FakeLease:
    def __init__(self, connection):
        self.connection = connection
        self.exited = False

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc_value, traceback):
        self.exited = True


def test_upload_advisory_lock_is_explicitly_unlocked_before_pool_return(monkeypatch):
    connection = FakeConnection()
    lease = FakeLease(connection)
    monkeypatch.setattr(assets, "connection_lease", lambda: lease)

    upload_lock = assets.try_acquire_upload_lock("f" * 64)
    assert upload_lock is not None

    assets.release_upload_lock(upload_lock)

    assert "pg_try_advisory_lock" in connection.statements[0][0]
    assert "pg_advisory_unlock" in connection.statements[1][0]
    assert connection.statements[0][1] == connection.statements[1][1]
    assert lease.exited is True
