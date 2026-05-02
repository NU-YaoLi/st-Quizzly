"""Unit tests for Supabase-backed daily generation rate limit."""

import unittest
from unittest.mock import MagicMock, patch


class TestDailyGenerationRateLimit(unittest.TestCase):
    def test_disabled_always_allows(self):
        with patch("bknd.quizzly_rate_limit.rate_limit_disabled", return_value=True):
            from bknd.quizzly_rate_limit import check_daily_generation_allowed

            r = check_daily_generation_allowed()
            self.assertTrue(r.allowed)

    def test_no_service_role_skips_enforcement(self):
        with patch("bknd.quizzly_rate_limit.rate_limit_disabled", return_value=False), patch(
            "bknd.quizzly_rate_limit._supabase_config",
            return_value=("https://example.supabase.co", None),
        ):
            from bknd.quizzly_rate_limit import check_daily_generation_allowed

            r = check_daily_generation_allowed()
            self.assertTrue(r.allowed)

    def test_allows_when_under_limit(self):
        with patch("bknd.quizzly_rate_limit.rate_limit_disabled", return_value=False), patch(
            "bknd.quizzly_rate_limit._supabase_config",
            return_value=("https://example.supabase.co", "sr_key"),
        ), patch(
            "bknd.quizzly_rate_limit.count_generations_today",
            return_value=(2, None),
        ):
            from bknd.quizzly_rate_limit import check_daily_generation_allowed

            r = check_daily_generation_allowed()
            self.assertTrue(r.allowed)
            self.assertEqual(r.used_today, 2)

    def test_blocks_when_at_limit(self):
        with patch("bknd.quizzly_rate_limit.rate_limit_disabled", return_value=False), patch(
            "bknd.quizzly_rate_limit._supabase_config",
            return_value=("https://example.supabase.co", "sr_key"),
        ), patch(
            "bknd.quizzly_rate_limit.count_generations_today",
            return_value=(3, None),
        ):
            from bknd.quizzly_rate_limit import check_daily_generation_allowed

            r = check_daily_generation_allowed()
            self.assertFalse(r.allowed)
            self.assertIn("limit reached", r.message.lower())
            self.assertIn("try again in", r.message.lower())
            self.assertIn("midnight utc", r.message.lower())

    def test_db_error_is_not_allowed(self):
        with patch("bknd.quizzly_rate_limit.rate_limit_disabled", return_value=False), patch(
            "bknd.quizzly_rate_limit._supabase_config",
            return_value=("https://example.supabase.co", "sr_key"),
        ), patch(
            "bknd.quizzly_rate_limit.count_generations_today",
            return_value=(None, "connection refused"),
        ):
            from bknd.quizzly_rate_limit import check_daily_generation_allowed

            r = check_daily_generation_allowed()
            self.assertFalse(r.allowed)
            self.assertIn("connection refused", r.message)

    def test_hash_client_ip_stable(self):
        with patch("bknd.quizzly_rate_limit._ip_salt", return_value="test-salt"):
            from bknd.quizzly_rate_limit import hash_client_ip

            a = hash_client_ip("203.0.113.1")
            b = hash_client_ip("203.0.113.1")
            c = hash_client_ip("203.0.113.2")
            self.assertEqual(a, b)
            self.assertNotEqual(a, c)

    def test_record_skips_when_disabled(self):
        with patch("bknd.quizzly_rate_limit.rate_limit_disabled", return_value=True):
            from bknd.quizzly_rate_limit import record_successful_generation

            err = record_successful_generation("anyhash")
            self.assertIsNone(err)

    def test_record_insert_calls_table(self):
        mock_sb = MagicMock()
        mock_sb.table.return_value.insert.return_value.execute.return_value = MagicMock()

        with patch("bknd.quizzly_rate_limit.rate_limit_disabled", return_value=False), patch(
            "bknd.quizzly_rate_limit._client",
            return_value=mock_sb,
        ):
            from bknd.quizzly_rate_limit import record_successful_generation

            err = record_successful_generation("abc123hash")
            self.assertIsNone(err)
            mock_sb.table.assert_called_with("quiz_generation_usage")
            mock_sb.table.return_value.insert.assert_called_once_with(
                {"ip_hash": "abc123hash", "estimated_cost_usd": None}
            )


if __name__ == "__main__":
    unittest.main()
