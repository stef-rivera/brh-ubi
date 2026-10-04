# Correcting local sign recognition

Recommendation: add a **Correct / Not a traffic sign** action to each captured sign. A correction should fix the current log and practice immediately, remember the reviewed example locally for replay, and separately become an annotation for later fine-tuning. These are three different effects; clicking a label does not update neural-network weights.

## What exists now

The live pipeline uses LISA47 YOLOv8m, a general YOLOE candidate detector, Apple Vision OCR, and local CLIP text/image similarity. LISA has 47 trained categories; the actual checkpoint does **not** include no-turn-on-red, road-work, or combined bicycle/pedestrian crossing. CLIP supplies nearest-label guesses for these and other unsupported crops. Training LISA alone will not suppress all false candidates from YOLOE or repair CLIP's fallback guesses.

The specialist live result contains three resolved and thirteen tentative rows, including street blades and business boards guessed as traffic instructions. Its own report records no-turn-on-red being guessed incorrectly. A corrected catalog meaning or new CLIP prompt changes application behavior, but does not teach LISA a new class. See `research/vision-architecture/specialist-live-results.json` and `specialist-source-survey.json` for measured evidence and inspected class names.

## Smallest useful correction feature

1. Choose **Correct label**, **Not a traffic sign**, or **Ignore for this demo** on a row; show the raw image and allow editing the box, exact text, and speed value. “Ignore” is a product choice, useful for real green street-name signs, not a claim that the detector hallucinated them.
2. Save the original prediction and human correction separately. Include event/track identity, clip SHA256, video time, original-frame dimensions and box, raw crop and raw frame, model/version/checksum, and reviewer/time. Existing red-outlined display thumbnails are unsuitable as training images.
3. Immediately replace that row's explanation and rebuild its parked question; remove false-positive rows from practice and cancel queued audio for that row. Retain prediction scores and show human provenance internally. Do not rename human feedback as a model-confidence increase.
4. Persist a local example memory. For this unchanged clip, use clip hash plus approximate time/position and raw visual similarity to match the same tracked sign on replay, not event IDs (which change each drive). Propagate within the active track. Version and allow undo of the correction. A near match on an unrelated sign must not globally overwrite its prediction.
5. For new clips, an optional local embedding-neighbor memory can suggest the reviewed label from visually similar crops. Keep this separate from detector scores and model weights. It improves only cases that match; it does not recover signs the detector never finds.

This is the fastest route to a repeatable corrected demo. Clip-specific replay memory should be named **human-corrected replay**, not presented as general model accuracy. A fully scripted timestamp playback is a different demo mode and must not masquerade as live recognition.

## Actual training that improves new-video recognition

Collect reviewed raw frames from **different drives**, with multiple independent physical examples of each failure class, varied scale/lighting, and hard negatives such as business boards and street blades. Label every target object in a selected full frame; a deleted false box does not justify treating other real signs in that frame as background. Review whole negative frames before exporting them with no boxes.

Use local Label Studio for a light Mac setup: run it in a separate Python environment and export YOLO boxes. Its documentation covers [local installation](https://labelstud.io/guide/install.html) and [annotation export](https://labelstud.io/guide/export.html). Self-hosted CVAT is useful for annotating tracks across longer videos but introduces Docker/services; its [installation](https://docs.cvat.ai/docs/administration/basics/installation/) and [YOLO export](https://docs.cvat.ai/docs/manual/advanced/formats/format-yolo/) describe that workflow. No image uploads are required by the proposed local setup.

Keep an immutable class map: retain the 47 checkpoint class IDs and append missing classes if fine-tuning this detector, or deliberately train a separate smaller classifier with its own map. New output classes require a compatible detection head and training examples; adding names to our catalog is insufficient. Rehearse transfer into a new head without assuming original output channels automatically preserve their meaning. Mix original-class examples into training to reduce forgetting. New-class support and original-class retention both need evaluation.

[Ultralytics dataset format](https://docs.ultralytics.com/datasets/detect/) uses per-image normalized boxes (`class center_x center_y width height`) and a YAML class/split map. Split by source video, ideally by drive/location and physical sign, **before** extracting frames. Adjacent frames of one sign in both training and validation inflate results. Our six raw human-reviewed examples are explicitly `evaluation_only_do_not_train`; keep their entire source clip out of training for an independent claim. If we train on this minute for a tailored demo, call subsequent results replay-fit results and create a new independent evaluation set.

Ultralytics supports pretrained fine-tuning on [Apple MPS](https://docs.ultralytics.com/modes/train/). Start a measured smoke run on the existing Mac with small batch size, `device="mps"`, and `workers=0`; freeze some backbone layers if appropriate. Do not promise eight-hour training or a budget without timing epochs on the actual corrected dataset. Local training has no model API usage fee; it consumes machine time/memory and can compete with the running demo.

Run [validation](https://docs.ultralytics.com/modes/val/) on held-out drives, then replay the complete test video through the whole pipeline. Measure per-class misses/wrong labels, false announcements per minute, duplicate announcements, time from first sighting to final label and audio, and FPS. Detection mAP alone does not establish a good spoken demo. Fine-tuning the detector cannot repair OCR text errors or delayed/duplicate audio by itself.

## Deployment requirements

Keep the old weights available for rollback. The current `backend/us_signs.py` rejects weights with a different hard-coded SHA256, so a trained checkpoint cannot simply replace `.local/vision/lisa47.pt`. Add a reviewed model manifest containing local weights path, checksum, architecture, class names, dataset version and evaluation report; retain restricted checkpoint loading. Update specialist-to-catalog mapping and add catalog/practice entries for genuinely new categories.

Effort estimates, not measured timings: correcting this minute's rows is a small annotation task; an integrated correction UI/memory is an implementation task; dependable fine-tuning requires collecting examples across multiple drives and a train/evaluate/deploy loop. Start with instant corrections and raw-data capture, then train in batches. Neither feedback memory nor a short fine-tune guarantees every future sign is right.

## Suggested record

```json
{
  "schema_version": 1,
  "video_sha256": "sha256-of-source-video",
  "track_time_seconds": [6.6, 9.4],
  "frame_time_seconds": 9.16,
  "box_xyxy": [885, 345, 942, 408],
  "frame_size_wh": [1280, 720],
  "raw_frame_path": "local/path/frame.png",
  "raw_crop_path": "local/path/crop.png",
  "predicted_label": "road_work",
  "predicted_source": "local-symbol",
  "model_sha256": "sha256-of-model",
  "action": "correct_label",
  "corrected_label": "no_turn_on_red",
  "corrected_text": "NO TURN ON RED",
  "corrected_value": null,
  "reviewed_by": "local-reviewer",
  "split_group": "source-drive-id",
  "dataset_use": "review_required"
}
```

The record illustrates the proposal; it is not a newly collected annotation or an implemented feedback API.

## Training time and sharing with teammates

There is **no measured training benchmark on this M3 Max yet**. The live inference benchmark cannot be converted directly to training throughput. For planning only: a small correction fine-tune of roughly 100–500 reviewed frames, 20–40 epochs, 640px images and a small batch may take about **30 minutes to 3 hours** of training. Adding missing classes with thousands of varied frames and 50–100 epochs is **several hours, overnight, or longer**. Data collection, annotation, evaluation and implementation are additional work. 1280px images, full-backbone training, validation frequency, augmentation and memory pressure can substantially increase time. These ranges are estimates, not measured promises.

Before a long run, time two or three warmed epochs using the actual dataset, image size and batch. Estimate remaining duration as median seconds per epoch times remaining epochs, plus final validation; stop if loss/labels/configuration are clearly wrong. Instant correction memory needs **zero training time**. A finite recorded demo can therefore be corrected sooner than a generalized detector can be trained.

**Yes: teammates can use the trained weights without retraining.** The learned parameters live in `best.pt`; teammates need those weights, matching architecture/class map and inference dependencies, not the training dataset or optimizer state. Share the complete application's local dependencies too: YOLOE/CLIP assets and Mac Vision OCR remain separate from the specialist. Windows/Linux need an OCR replacement and a supported device instead of our currently hard-coded MPS route. Sharing weights does not itself make the Mac-specific app portable.

Recommended packaging:

1. Attach inference weights to a versioned **GitHub Release**, e.g. `us-signs-v2`, rather than adding every binary revision to Git history. Commit a manifest containing a pinned release asset URL, SHA256, class names, architecture, tested dependency versions, data/evaluation version, metrics and attribution/terms.
2. Make the setup script download the exact asset once, verify its checksum, and cache it under ignored `.local/vision/`. Avoid a mutable “latest” URL when reproducibility matters. Update the loader to accept this reviewed manifest instead of the original hard-coded checkpoint hash.
3. Teammates clone/pull, run setup, then launch. They run inference immediately; training remains a separate optional command. Retain the previous release for rollback.

The current specialist file is about 52MB and `*.pt` is Gitignored. GitHub [warns for regular Git files over 50 MiB and blocks files over 100 MiB](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github). A current-size file may fit direct Git, but repeated checkpoints bloat clone history. [Release assets](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases) support files under 2 GiB. [Git LFS](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-git-large-file-storage) is another option but adds LFS setup/storage concerns; releases plus a download manifest are simpler here.

Sharing is technically straightforward; redistribution terms are a separate check. The [checkpoint repository](https://github.com/ali-haidous/yolo_trafficsign) states MIT for its project, while the [original LISA dataset](https://cvrr.ucsd.edu/lisa-traffic-signs-dataset) states an academic license. The upstream MIT declaration alone does not verify unrestricted redistribution or commercialization of derived weights/data. [Ultralytics licensing guidance](https://www.ultralytics.com/license) says its framework and trained models use AGPL-3.0 by default, with Enterprise terms for proprietary deployments. Preserve attribution and verify applicable terms before public redistribution/commercial use; no license clearance, upload, or training has been performed in this task.
