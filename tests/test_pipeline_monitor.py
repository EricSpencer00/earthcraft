"""A recent publication does not make old or missing host telemetry healthy."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import monitor


class PipelineTelemetryTests(unittest.TestCase):
    def report(self, status, overrides_commit=None):
        commit={'title':'Generate tiles/v2: 1 tile(s) 1_2', 'date':datetime.now(timezone.utc).isoformat()}
        result=monitor.MonitorReport()
        commit.update(overrides_commit or {})
        with patch.object(monitor, 'fetch_json', side_effect=[[commit],status]):
            monitor.check_pipeline_freshness(result)
        return result

    def status(self, **overrides):
        return dict(schema='earthcraft.pipeline-status.v1',time_utc=datetime.now(timezone.utc).isoformat(),
                    service='active',disk={'free_bytes':33*2**30},tiles_last_24h=1121,**overrides)

    def test_recent_active_report(self):
        self.assertTrue(self.report(self.status()).is_healthy())

    def test_missing_report_cannot_pass(self):
        self.assertFalse(self.report(None).checks['Pipeline status'][0])

    def test_old_report_cannot_pass(self):
        status=self.status();status['time_utc']=(datetime.now(timezone.utc)-timedelta(hours=4)).isoformat()
        self.assertFalse(self.report(status).checks['Pipeline status'][0])

    def test_clean_stop_is_idle_even_with_old_tiles(self):
        status=self.status(workers=0);status['service']='inactive'
        status['time_utc']=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()
        old={'date':(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()}
        result=self.report(status,old)
        self.assertTrue(result.is_healthy());self.assertIn('Idle',result.checks['Pipeline freshness'][1])

    def test_crashed_or_stale_active_service_cannot_pass(self):
        status=self.status();status['service']='failed'
        self.assertFalse(self.report(status).checks['Pipeline status'][0])
        old={'date':(datetime.now(timezone.utc)-timedelta(hours=9)).isoformat()}
        self.assertFalse(self.report(self.status(),old).is_healthy())
        status=self.status(workers=2);status['service']='inactive'
        self.assertFalse(self.report(status).checks['Pipeline status'][0])

    def test_bad_timestamp_cannot_pass(self):
        for stamp in ['', None, '2026-10-18T12:00:00']:
            status=self.status();status['time_utc']=stamp
            self.assertFalse(self.report(status).checks['Pipeline status'][0])


if __name__=='__main__':
    unittest.main()
