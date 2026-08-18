"""`_PooledConn` 위임 계약 — enterprise repository 가 트랜잭션 설정을 쓸 수 있어야 한다."""

from agent.db.connection import _PooledConn


class _FakeConn:
    def __init__(self) -> None:
        self.autocommit = True
        self.closed = False


class _FakePool:
    def __init__(self) -> None:
        self.returned: list[_FakeConn] = []

    def putconn(self, conn: _FakeConn) -> None:
        self.returned.append(conn)


def test_pooled_conn_reads_wrapped_attribute():
    conn = _FakeConn()
    wrapper = _PooledConn(conn, _FakePool())

    assert wrapper.autocommit is True


def test_pooled_conn_writes_through_to_wrapped_connection():
    conn = _FakeConn()
    wrapper = _PooledConn(conn, _FakePool())

    wrapper.autocommit = False

    assert conn.autocommit is False
    assert wrapper.autocommit is False


def test_pooled_conn_close_returns_connection_to_pool():
    conn = _FakeConn()
    pool = _FakePool()
    wrapper = _PooledConn(conn, pool)

    wrapper.close()

    assert pool.returned == [conn]
