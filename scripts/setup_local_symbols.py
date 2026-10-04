"""Download local CLIP weights once. No cloud inference is used."""
from pathlib import Path
import subprocess,sys
root=Path(__file__).resolve().parents[1]
subprocess.run([sys.executable,'-m','pip','install','git+https://github.com/ultralytics/CLIP.git@a13192f8cb767260d7dfd98c843b0716593169e7'],check=True)
import clip
clip.load('ViT-B/32',device='cpu',download_root=str(root/'.local/vision'))
print('Local symbol classifier ready.')
