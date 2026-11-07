from datetime import timedelta
from unittest.mock import patch
from .test_voice import VoiceTests


class DemoCallOverrideTests(VoiceTests):
    def test_override_is_dated_protected_and_preserves_pin(self):
        self.now=self.now.replace(hour=16)  # 23:00 WIB
        with patch.dict('os.environ',{'DEMO_NO_CALL_LIMITS':'1','DEMO_NO_CALL_LIMITS_DATE':'2026-11-07'}):
            self.assertEqual(self.call(diagnostic=True)['status'],'queued')
            self.assertEqual(self.call(diagnostic=True)['status'],'queued')
            self.client.post('/api/auth/logout')
            self.assertEqual(self.client.post('/api/voice/twilio/call',json={'responder_id':'r1','diagnostic':True}).status_code,401)
            self.now+=timedelta(days=1)
            self.assertEqual(self.call(diagnostic=True)['reason'],'quiet_hours')
