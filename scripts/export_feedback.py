#!/usr/bin/env python3
"""Export reviewed recognition crops, not incomplete detector training labels.

Whole frames contain other unannotated signs. This export is a review queue;
training_ready remains false until complete object annotation is performed.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import feedback


def export(destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    examples = []
    with feedback._lock:
        records = feedback._read()
    for record in records:
        action = record.get('feedback', {}).get('action')
        if action not in ('confirm', 'correct', 'not_sign', 'ignore'):
            continue
        row = record.get('reviewed_row', {})
        assets = {}
        for kind, relative in record.get('assets', {}).items():
            source = (feedback.DIRECTORY / relative).resolve()
            if not source.is_relative_to(feedback.DIRECTORY.resolve()) or not source.is_file():
                continue
            target = destination / 'images' / source.name
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(source, target)
            assets[kind] = str(target.relative_to(destination))
        examples.append({'key': record['key'], 'source_sha256': record.get('source_sha256'), 'ts_video': record.get('ts_video'), 'box': record.get('box'), 'action': action, 'sign_id': row.get('sign_id'), 'sign_text': row.get('sign_text'), 'value': row.get('value'), 'images': assets, 'original_prediction': record.get('original_prediction'), 'frame_objects_fully_reviewed': False})
    manifest = {'format': 'road-coach-human-review-v1', 'training_ready': False, 'purpose': 'Recognition crop review and detector annotation queue', 'notes': ['Complete all sign boxes in each full frame before YOLO detector training.', 'Ignore means a real but unwanted sign; not_sign means a false detection.', 'Split evaluation by source_sha256; repeated frames of one sign are not independent examples.', 'No evaluation-only ground truth is included automatically.'], 'examples': examples}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = export(args.output)
    print(f"Exported {len(manifest['examples'])} reviewed examples to {args.output}. Training ready: false.")
