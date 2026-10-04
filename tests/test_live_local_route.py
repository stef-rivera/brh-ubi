import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.main import app

class LocalOnlyRouteTests(unittest.TestCase):
    def test_cloud_reader_requests_are_rejected_before_pipeline_changes(self):
        with patch('backend.main._stop_pipeline') as stop, TestClient(app) as client:
            for body in ({'detector':'chatgpt'}, {'detector':'gemini'}, {'detector':'local','cloud_assist':True}):
                response=client.post('/api/drive/start',json=body)
                self.assertEqual(response.status_code,400)
                self.assertIn('locally',response.json()['detail'])
            stop.assert_not_called()

    def test_default_start_constructs_only_local_reader(self):
        with patch('backend.local_vision.LocalDetector') as local, patch('backend.main.VideoSource') as video, patch('backend.chatgpt_client.read_frame') as cloud, patch('backend.main._resolve_source',return_value='clip.mp4'), TestClient(app) as client:
            response=client.post('/api/drive/start',json={'source':'file'})
            self.assertEqual(response.status_code,200)
            local.assert_called_once_with(video.return_value)
            cloud.assert_not_called()
