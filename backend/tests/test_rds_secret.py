from __future__ import annotations
from unittest.mock import MagicMock, patch

from chatroom_api import rds


def test_connection_params_use_secret_fallbacks():
    with patch("chatroom_api.rds._secret_cache", None), \
         patch("chatroom_api.rds.config.RDS_SECRET_ARN", "arn:test"), \
         patch("chatroom_api.rds.config.RDS_HOST", ""), \
         patch("chatroom_api.rds.config.RDS_PORT", 5432), \
         patch("chatroom_api.rds.config.RDS_DATABASE", ""), \
         patch("chatroom_api.rds.config.RDS_USERNAME", ""), \
         patch("chatroom_api.rds.config.RDS_PASSWORD", ""), \
         patch("chatroom_api.rds.boto3.client") as client_mock:
        secret_client = MagicMock()
        secret_client.get_secret_value.return_value = {
            "SecretString": (
                '{"host":"db.example","port":5439,"dbname":"stimulize",'
                '"username":"stim","password":"pw"}'
            )
        }
        client_mock.return_value = secret_client

        params = rds._connection_params()

    assert params == {
        "host": "db.example",
        "port": 5432,
        "database": "stimulize",
        "user": "stim",
        "password": "pw",
    }


def test_get_connection_passes_resolved_params_to_pg8000():
    fake_conn = MagicMock()
    fake_pg8000 = MagicMock()
    fake_pg8000.connect.return_value = fake_conn
    with patch("chatroom_api.rds._conn", None), \
         patch("chatroom_api.rds._connection_params", return_value={
             "host": "db.example",
             "port": 5432,
             "database": "stimulize",
             "user": "stim",
             "password": "pw",
         }), \
         patch("chatroom_api.rds.pg8000.dbapi", fake_pg8000):
        conn = rds._get_connection()

    fake_pg8000.connect.assert_called_once_with(
        host="db.example",
        port=5432,
        database="stimulize",
        user="stim",
        password="pw",
    )
    assert conn is fake_conn
    assert conn.autocommit is True
