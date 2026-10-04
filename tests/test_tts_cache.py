import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from backend import tts

class AudioCacheTests(unittest.TestCase):
 def test_cached_audio_plays_without_credentials_or_cloud_call(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory); name=hashlib.sha256(b"Stop sign ahead.").hexdigest()[:16]+".mp3"
   (root/name).write_bytes(b"cached-audio")
   with patch.object(tts,'AUDIO',root), patch.object(tts,'settings',SimpleNamespace(elevenlabs_voice_id='',elevenlabs_api_key='')), patch.object(tts,'ElevenLabs') as cloud:
    self.assertEqual(tts.speak(' Stop sign   ahead. '),'/audio/'+name)
    cloud.assert_not_called()
 def test_new_audio_generates_once_then_reuses_file(self):
  with tempfile.TemporaryDirectory() as directory:
   client=Mock();client.text_to_speech.convert.return_value=iter([b"audio"])
   settings=SimpleNamespace(elevenlabs_voice_id='voice',elevenlabs_api_key='key',elevenlabs_model='model')
   with patch.object(tts,'AUDIO',Path(directory)),patch.object(tts,'settings',settings),patch.object(tts,'_client',client):
    first=tts.speak('School sign ahead.')
    self.assertTrue(first);self.assertEqual(tts.speak('School sign ahead.'),first)
    self.assertEqual(client.text_to_speech.convert.call_count,1)
