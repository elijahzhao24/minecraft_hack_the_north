# Forced front/back body merge

This branch assumes two stationary phones **180 degrees apart**, seeing the
front and back of the same person at approximately the same time. The backend
now applies a body-space correction automatically on every capture pair. No
shared surface points, manual ICP trigger, or Minecraft update is needed.
Restart the Python backend, keep the torso and legs visible in both phones,
and press **V** in Minecraft for live capture. **Y** shows camera contributions.

The front phone is the reference (`HMC_FRONT_DEVICE_ID=front-phone` by default).
The other device is the rear phone even if its configured ID is `side-phone`.
The existing board setup, session/movement checks, and capture-readiness rules
still apply. A nominal rig still requires explicit simulation mode; forced body
assembly itself works with either board or nominal poses.

## The hard constraints

1. Force the rear camera's horizontal view direction to oppose the front one,
   retaining each phone's pitch/roll. This corrects nominal side-camera yaw too.
2. Reconstruct both masked views **before stage cropping**. The real five-meter
   camera-distance gate, confidence filtering, and person mask still run first.
3. Estimate the central body profile, favoring observed hips and shoulders when
   available. Ignore outstretched arms when calculating it. Align vertical body
   anchors (shoulders, hips, knees, ankles); use the lower silhouette if those
   observations are unavailable.
4. Constrain slices from the lower legs through the torso independently. Move
   and adjust the rear core's width to the front profile, so one lateral offset
   at the chest cannot leave a second set of displaced legs below it. Blend the
   corrections continuously between heights. Points outside the core, including
   extended arms, receive a translation rather than being scaled into the torso.
5. Bring the shells' inward depth envelopes together with a default **1 cm
   overlap**. Keep the rear surface behind the front surface, with at least
   **12 cm median separation** where the observations are near-flat. This small
   thickness prior prevents two flat captures from becoming the same sheet.
6. Apply the same map to per-view depth landmarks before fitting colliders.
   Disable camera-ray triangulation in the deformed space. RGB diagnostic
   overlays use the inverse map; Minecraft points, skeletons, and hitboxes
   consequently use the same assembled coordinates.

This is **visual assembly**, not recovered physical camera calibration. It can
compress/stretch the rear body core to fit the front silhouette. The 12 cm floor
is an assumption for thin/flat observations, not a measured body dimension.
If neither camera sees the side seam, this cannot invent those missing points
or guarantee a watertight mesh. Different leg poses or occlusion can still leave
local artifacts; keep legs steady during a setup capture. Each half's measured
colors and source bits are retained, and the saved calibration is never changed.

## Settings

The defaults apply without changing the launch command:

```bash
HMC_OPPOSING_BODY_MERGE=true
HMC_BODY_MERGE_MIN_THICKNESS_M=0.12
HMC_BODY_MERGE_SEAM_OVERLAP_M=0.01
```

Set these as environment variables when starting the backend or in `backend/.env`.
The minimum thickness must be greater than zero and at most 0.4 m; overlap is
0–0.03 m. Use `HMC_OPPOSING_BODY_MERGE=false` for oblique/same-side rigs, original
calibration inspection, or the oblique synthetic fixture demo. Restart after
changing settings. Increasing overlap is not a substitute for visible body data.

## Diagnostics and failures

`GET /rig/register` includes `body_merge`. `/health` includes the same report in
each phone's reconstruction diagnostics. Successful frames carry
`forced_opposing_body_merge`, deliberately identifying the visual correction.
The report includes reference/rear IDs, slice count, maximum lateral/depth
correction, vertical shift, and whether the thickness prior was needed.

If tracking is limited, a view is missing, there is insufficient torso/lower-body
depth, or widths differ by more than a factor of two, the backend keeps available
points and emits `body_merge_unavailable:<reason>`. It never reuses a previous
person's warp to disguise missing observations. Missing-contribution and stale
frame warnings remain active. No alignment method can merge a view that the
phone or person mask did not supply.

## Verification

Automated regressions use separate front/back half-shells with no common skin
points, meter-scale displacement, vertical error and lean. They check torso and
both-leg alignment, thickness preservation, planar fallback, arm independence,
source/color retention, invertible overlays, warped landmark fusion, and recovery
of a rear cloud that would otherwise be removed entirely by stage cropping.

On the rig, inspect a still, full-body capture with Y. Both torso halves should
occupy one body and the legs should align. Rotate the Minecraft view to inspect
thickness, then move each arm. Cover a camera and verify the explicit unavailable
warning. Real capture recordings are still needed to tune the assumptions for
your exact phone placement and segmentation quality.
