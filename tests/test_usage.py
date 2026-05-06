import tempfile
import unittest
from pathlib import Path

from app.usage import UsageMonitor, format_bytes, format_usage_report, integrate_bandwidth_out


class UsageTests(unittest.TestCase):
    def test_integrates_hetzner_bandwidth_out(self) -> None:
        data = {
            "metrics": {
                "time_series": {
                    "network.0.bandwidth.out": {
                        "values": [
                            ["2026-05-01T00:00:00+00:00", "10"],
                            ["2026-05-01T00:01:00+00:00", "20"],
                            ["2026-05-01T00:02:00+00:00", "30"],
                        ]
                    }
                }
            }
        }

        self.assertEqual(integrate_bandwidth_out(data), 3600)

    def test_alert_thresholds_are_sent_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            monitor = UsageMonitor(
                alert_step_percent=10,
                alert_state_file=str(Path(temp_dir) / "alerts.json"),
                openai_monthly_budget_usd=20,
            )

            self.assertEqual(monitor.next_hetzner_alert_percent(21.5), 20)
            monitor.mark_hetzner_alert_sent(20)
            self.assertEqual(monitor.next_hetzner_alert_percent(21.5), 0)
            self.assertEqual(monitor.next_hetzner_alert_percent(30.0), 30)
            self.assertEqual(monitor.next_openai_alert_percent(19.9), 10)
            monitor.mark_openai_alert_sent(10)
            self.assertEqual(monitor.next_openai_alert_percent(19.9), 0)
            self.assertEqual(monitor.next_openai_alert_percent(20.0), 20)

    def test_openai_alerts_require_budget(self) -> None:
        monitor = UsageMonitor(alert_step_percent=10, openai_monthly_budget_usd=0)

        self.assertEqual(monitor.next_openai_alert_percent(50), 0)

    def test_formats_unconfigured_report(self) -> None:
        monitor = UsageMonitor()

        report = format_usage_report(monitor._snapshot_sync())

        self.assertIn("Hetzner: non configurato", report)
        self.assertIn("OpenAI: monitor costi non configurato", report)

    def test_format_bytes(self) -> None:
        self.assertEqual(format_bytes(1024**4), "1.00 TB")


if __name__ == "__main__":
    unittest.main()
