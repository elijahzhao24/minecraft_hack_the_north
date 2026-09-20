# Two fixed phones: camera setup

Camera calibration measures where both phones sit in one physical coordinate
system. Avatar normalization only sizes/places that scan in Minecraft.

## Print and place the board

Open [calibration-board.svg](calibration-board.svg) in a browser or vector viewer
and print on **A3 at 100% / actual size**. Disable fit-to-page. Measure the 50 mm
line and at least one 40 mm square with a ruler. The board is 280 × 400 mm;
markers are 30 mm, dictionary DICT_5X5_100. Do not resize an A3 print onto A4.
Regenerate with `cd backend && uv run python scripts/print_calibration_board.py`.

Place the sheet flat, printed side up, on the floor between the two phones.
Its printed top-left corner defines stage origin; its bottom edge points toward
`front-phone`. Stage +Y is up, +X runs right across the print, and +Z runs down
the print toward the front phone. Keep the sheet flat and stationary.

Mount both phones in their final positions, with the board visible in both
camera views. They can observe it from opposite sides. Step out of the view.
Do **not** tip a phone down for calibration and then tip it back up. If the
board is not visible or occupies too little of the frame, reposition the fixed
rig before starting. Keep the person near the marked stage afterward.

## Calibrate in Minecraft

1. Start the backend and connect the phones as `front-phone` and `side-phone`.
   No existing calibration file is needed. Wait for normal ARKit tracking.
2. Press **N**, or run **`/humancraft cameras calibrate`**.
3. Read the HUD: setup collects at 3 pairs/second, needs 12 accepted observations
   per phone, and times out after 30 seconds. Ordinary live capture pauses.
4. Wait for **calibration_ready**. Failure messages identify the phone and
   rejection reason. Failures keep the previous rig; they do not install a
   guessed alignment. Raw board packets are saved under `backend/data/recordings`.
5. Remove the board without touching the phones. Stand at the capture mark and
   resume capture. Previous live mode resumes automatically.

`/humancraft normalize` resets avatar scale. The old `/humancraft calibrate`
command remains a normalization alias and explains the distinction.

Recalibrate after restarting either phone's AR session, changing image format,
or moving a phone. Movement over 2 cm or 2° on three normally tracked frames
invalidates the rig. Old person-ICP correction files are ignored. Existing board
files without session baselines require one guided recalibration. Synthetic
fixtures require explicit `HMC_SIMULATION_MODE=true` at runtime; their nominal geometry is not a physical calibration.

## Diagnose a missing side

Press **Y** to display actual contributing cameras: first/source `front-phone`
is cyan, second/source `side-phone` is magenta, shared voxels are yellow. Gray
means a legacy frame has no source metadata; it does not indicate camera failure.

A missing view leaves the available scan visible with `missing_view:<device>:<step>`.
`GET /health` reports connection and clock readiness, frame age, tracking,
raw valid depths, and counts after range, confidence, mask, stage crop, and merge.
No new pair produces a stale scan label; stale server collision data still expires.
A phone sending no packet cannot form a new pair, so the last scan stays visible.

Interpret losses in order: no raw depth suggests sensor/capture trouble;
range/confidence losses concern depth quality or distance; mask losses concern
person segmentation; stage-crop losses concern position/calibration. Do not use
nearest-point alignment between the front and back of a person: distinct surfaces
can collapse together while reporting excellent matching error.

## Five-meter cutoff

Swift encoding, its depth preview, and Python independently reject samples whose
3D distance from the capturing phone exceeds 5 meters. The RGB camera preview is
unchanged. The backend `HMC_MAX_RANGE_M` may reduce this limit but cannot exceed 5.
The older forward-depth, confidence, foreground-mask and stage-box limits still
apply. Invalid/out-of-range depth is zero on the wire and black in the preview.
This is sample filtering, not a command that changes LiDAR hardware sensing range.

## HTTP/control compatibility

- `POST /rig/register` or WebSocket `register_rig` starts **board** calibration,
  not body ICP. An accepted request is not evidence that calibration succeeded.
- `GET /rig/register` returns state (`collecting`, `validating`, `ready`, `failed`),
  per-device accepted counts/rejections, error, active calibration ID and range.
- `DELETE /rig/register` cancels setup; it preserves a previously validated rig.
- Existing ACK messages carry progress/results to Minecraft. Character-frame v2
  optionally adds `point_sources`, a `uint8` buffer of shape `[point_count]`.
  Bit 0 refers to `source_frames[0]`, bit 1 to `source_frames[1]`; shared voxels OR
  bits. Point geometry/color records and old fixture bytes are unchanged.

## Physical acceptance checklist

Measure camera spacing and compare it with the saved transforms. Capture a
stationary shared target and verify alignment within 3 cm. Then capture a person
from front/back and inspect each source color: body thickness must be preserved.
Cover each phone in turn; confirm a specific warning or stale-frame indication.
Place a target at 4.99, 5.00 and 5.01 meters, including near the image edges, and
check both depth preview and cloud. Save captures for replay if a failure remains.
Two views cannot reconstruct surfaces neither phone can see.
