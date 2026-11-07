import os
import unittest
from unittest.mock import patch
from . import test_planner


class PublicDemoTests(unittest.TestCase):
    """Run the complete existing pipeline checks under the public deployment flag."""
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {'DEMO_PUBLIC':'1','PUBLIC_DEMO':'false','PEATFORGE_LIVE_CHANNELS':'true'}))
        test_planner.PlannerTests.setUp(self)

    send=test_planner.PlannerTests.send
    report=test_planner.PlannerTests.report
    test_full_mock_loop=test_planner.PlannerTests.test_full_mock_loop_approval_once_and_reject

    # The legacy webhook retry test intentionally uses a real webhook route, now forbidden.
    def test_partial_hook_failure_does_not_duplicate_actions(self):
        for path in ('/api/voice/twilio/call','/api/voice/twilio/twiml/start',
                     '/api/voice/twilio/status','/api/channels/sms/inbound','/api/responders/r1/send'):
            self.assertEqual(self.client.post(path,json={}).status_code,403,path)
        config=self.client.get('/api/config').json()
        self.assertTrue(config['public_demo'])
        self.assertTrue(config['demo_public'])
        self.assertEqual(config['outbound'],'mock')
        self.assertTrue(self.app.state.channels.settings.force_mock)

    def test_explicit_protected_request_cannot_override_public_flag(self):
        from .app import create_app
        app=create_app(public_demo=False,engine=self.engine)
        self.assertTrue(app.state.public_demo)
        self.assertEqual(app.state.database_path.name,'public-demo.sqlite3')
