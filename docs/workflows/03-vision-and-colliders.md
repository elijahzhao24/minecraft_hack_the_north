# Workflow 3 — Vision, 3D landmarks, and colliders

## Outcome and boundary

This Python module converts two captured RGBD views plus rig calibration into:

- A person mask per view.
- Named body and hand observations with provenance/validity.
- Registered 3D landmarks in stage meters.
- A small, stable set of analytic colliders.

It is called synchronously by the backend's dedicated processor thread. It does not accept sockets, choose frame pairs, assign published frame IDs, or serialize `HMC1` envelopes.

## Models and packages

- MediaPipe Tasks Pose Landmarker: one person, segmentation masks enabled, `RunningMode.IMAGE` for frozen captures.
- MediaPipe Tasks Hand Landmarker: up to two hands, also in `IMAGE` mode.
- OpenCV/NumPy for image transforms, depth neighborhoods, triangulation, robust fitting, and overlays.

The Pose API exposes 33 pose landmarks, and asynchronous live mode may drop frames; snapshot processing therefore starts with deterministic image-mode calls. See the official [Pose Landmarker API](https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/vision/PoseLandmarker) and [MediaPipe vision module](https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/vision).

Pin model assets by SHA-256 in `backend/models/manifest.json`. A model filename without its hash/version is not a reproducible dependency. Do not download models during a demo startup.

## Module layout and public DTOs

```text
backend/src/hmc_backend/
├── vision/
│   ├── detector.py
│   ├── model_mapping.py
│   ├── hand_association.py
│   ├── depth_sampling.py
│   ├── registration.py
│   ├── triangulation.py
│   └── overlays.py
└── colliders/
    ├── models.py
    ├── fit_capsule.py
    ├── fit_hand.py
    ├── fit_foot.py
    ├── fit_torso.py
    └── validate.py
```

Inputs `CapturedFrame`, `PairedFrames`, `CameraCalibration`, `ViewDetection`, and `ColoredPointCloud` are defined in [../contracts.md](../contracts.md). The output is:

```python
@dataclass(frozen=True, slots=True)
class Landmark3D:
    name: str
    position_stage_m: tuple[float, float, float] | None
    valid: bool
    source: LandmarkSource
    confidence: float | None
    visibility: float | None
    observed_by: tuple[str, ...]
    reprojection_error_px: float | None

@dataclass(frozen=True, slots=True)
class FittedCharacter:
    source_capture_ids: tuple[UUID, UUID]
    calibration_id: UUID
    landmarks: tuple[Landmark3D, ...]
    colliders: tuple[Collider, ...]
    warnings: tuple[str, ...]
    subject_dimensions: SubjectDimensions
```

`Collider` is a frozen discriminated union of `SphereCollider`, `CapsuleCollider`, and `ObbCollider`. Invalid colliders use a separate `DisabledCollider(id, body_part, reason)` internal type; the transport adapter maps it to `valid=false` and no geometry. This prevents geometry functions from accidentally accepting a zero-sized placeholder.

## Detector initialization and outputs

Create one long-lived instance of each task, not one per image. Configuration starting point:

```python
pose_options = vision.PoseLandmarkerOptions(
    base_options=BaseOptions(model_asset_path=settings.pose_model_path),
    running_mode=vision.RunningMode.IMAGE,
    num_poses=1,
    min_pose_detection_confidence=0.5,
    min_pose_presence_confidence=0.5,
    min_tracking_confidence=0.5,
    output_segmentation_masks=True,
)

hand_options = vision.HandLandmarkerOptions(
    base_options=BaseOptions(model_asset_path=settings.hand_model_path),
    running_mode=vision.RunningMode.IMAGE,
    num_hands=2,
    min_hand_detection_confidence=0.5,
    min_hand_presence_confidence=0.5,
    min_tracking_confidence=0.5,
)
```

Thresholds are configuration and must be evaluated on saved poses rather than widened to make a single demo pass.

Convert decoded RGB NumPy arrays to `mp.Image` as sRGB. The detector returns a boolean/soft person mask in the original RGB raster, 33 named body observations, detected hand observations, and unregistered model-world priors. Preserve each model's visibility/presence exactly where supplied; do not create a joint confidence by averaging unrelated values.

## Hand crops and anatomical association

Run the first hand pass on the full-resolution image. If it misses small hands, derive padded square crops around each pose wrist/elbow direction, run hand inference on each crop, and map normalized crop coordinates back to full-image pixels through an explicit 3×3 affine transform.

For each candidate hand, score association to anatomical left/right using:

- Pixel distance between hand wrist and body wrist.
- Consistency with elbow→wrist direction.
- Model handedness after accounting for whether the image was mirrored (the transmitted image is not).
- Cross-view agreement after approximate 3D registration.

Require a margin over the alternate assignment. If ambiguous, keep the hand invalid rather than swapping it. Include an asymmetric right-arm-raised fixture as a permanent regression test.

## Depth-backed 3D observations

For each valid 2D landmark:

1. Map RGB pixel center to depth raster coordinates using the same uncropped scale convention as reconstruction.
2. Inspect a radius-2 initial neighborhood (configurable) limited by person mask and minimum depth confidence.
3. Reject zero/non-finite depth and samples separated from the local median by the discontinuity threshold (start at 5 cm or a relative threshold).
4. Require a minimum support count. Use a weighted median/robust estimate, not the nearest single pixel.
5. Unproject with `K_depth`, then transform through `T_stage_from_optical`.
6. Record support count, spread, device ID, and observation quality internally.

The visible surface is not automatically a joint center. Depth observations constrain rays/surfaces; registered anatomical priors and the other view constrain internal centers.

## Multiview triangulation and registration

For a landmark observed reliably in both RGB images:

- Form calibrated optical rays in the common stage frame.
- Solve the closest-points/least-squares ray intersection.
- Reject near-parallel rays, points behind either camera, high reprojection residual, or large disagreement with depth-supported positions.
- Prefer the well-supported depth estimate when triangulation geometry is weak; never blindly average incompatible points.

MediaPipe pose world landmarks are hip-centered and hand world landmarks are hand-centered. Register each prior into the stage frame using a robust similarity/rigid fit against reliable observed landmarks:

- Pose: shoulders, hips, elbows, knees when available; constrain scale using observed limb lengths.
- Hand: wrist plus MCP landmarks; attach the registered hand wrist to the reconciled body wrist.
- Reject a fit with too few non-collinear anchors, implausible scale, or large residual.

For each final landmark, choose provenance in priority order based on validated quality: compatible multiview/depth fusion, strong single-view depth neighborhood, registered prior constrained by observations, explicit derived center, or unavailable. The output `source` names the chosen path.

Derived landmarks:

- `body.pelvis_center`: midpoint of valid left/right hips.
- `body.head_center`: robust center estimated from valid face/ear/nose landmarks and local head surface; not simply the nose.

## Subject dimensions

Fit stable radii/widths from a clean neutral calibration pose and retain them as `SubjectDimensions` keyed by calibration/subject session. Later occluded poses may use these subject-specific values with `fit_source=subject_default`. Global defaults are last resort and are labeled.

```python
@dataclass(frozen=True, slots=True)
class SubjectDimensions:
    upper_arm_radius_m: SideValues
    forearm_radius_m: SideValues
    thigh_radius_m: SideValues
    shin_radius_m: SideValues
    hand_width_m: SideValues
    hand_thickness_m: SideValues
    foot_width_m: SideValues
    foot_thickness_m: SideValues
```

Values include estimate, source, sample count, and robust spread. Do not overwrite a good subject measurement with a noisy later pose.

## Collider set and fitting

Stable required IDs:

| ID | Body part | Geometry |
|---|---|---|
| `head` | `head` | sphere |
| `torso.chest` | `torso` | OBB |
| `torso.pelvis` | `pelvis` | OBB |
| `arm.{side}.upper` | upper arm | capsule |
| `arm.{side}.forearm` | forearm | capsule |
| `hand.{side}` | hand | OBB |
| `leg.{side}.thigh` | thigh | capsule |
| `leg.{side}.shin` | shin | capsule |
| `foot.{side}` | foot | OBB |

### Limb capsules

Use registered joint centers as segment endpoints. Select candidate surface points by per-view limb image region, distance to the 3D segment, source visibility, and separation from torso/neighbor limbs. Compute radial distances to the segment, remove end-cap and contamination outliers, then use a robust percentile/median-plus-margin capped to anatomical bounds. If fit support is insufficient, use the saved subject radius; otherwise disable.

Do not lengthen capsule endpoints beyond joints merely to catch a ray. Adjacent volumes may overlap slightly at joints, but gaps between arm and torso remain empty.

### Hand OBB

For each hand:

1. Longitudinal seed: wrist→middle MCP.
2. Transverse seed: pinky MCP→index MCP.
3. Normalize longitudinal; remove its component from transverse and normalize.
4. Third axis is their cross product; re-orthogonalize and validate determinant.
5. Project valid hand landmarks plus masked nearby hand surface into this basis.
6. Use robust min/max bounds including fingertips and a small documented padding (start 5 mm).
7. Require non-degenerate wrist/MCP geometry and enough observed finger extent. Otherwise use subject dimensions only if orientation remains observed; else disable.

The OBB covers the usable whole hand. A later palm-box plus finger-envelope capsule refinement may replace it without changing the external collider union.

### Foot OBB

Longitudinal direction is heel→foot-index. Infer lateral/vertical axes from local foot surface and registered pose. Use the calibrated floor normal only for a foot explicitly classified as planted; never flatten a lifted foot. Bounds include heel, sole, and forefoot. Width/thickness come from valid surface or saved subject dimensions, not shin radius.

### Torso/pelvis OBBs and head sphere

Build torso axes from shoulder/hip centers and the subject's front/back orientation, orthogonalize, then fit bounds to masked torso surface while excluding arms. Pelvis is separate to permit bent posture. Fit the head sphere robustly to head-region surface around registered head center; hair/clothing outliers are trimmed.

## Collider validation

Every collider passes pure validation before assembly:

- Unique stable ID and matching `body_part`.
- Finite coordinates, positive size, and configured anatomical upper bounds.
- Capsule endpoints are not coincident unless explicitly converted to sphere.
- OBB axes have norm 1, pairwise dot product near 0, and determinant magnitude near 1.
- Center/endpoints lie within the configured stage crop.
- Quality below the per-part minimum produces `DisabledCollider`, not padded geometry.

Pure geometry functions also expose ray intersection and cube overlap tests so Python fixtures can be compared with Java results using the same shapes.

## Debug artifacts

For every validation capture, write under a non-production debug flag:

- Per-camera RGB overlay with mask contour, body/hand IDs, validity, and sampled-depth locations.
- Stage cloud colored by source camera.
- Skeleton plus collider wireframe with stable IDs.
- JSON report containing observation sources, support counts, reprojection errors, fitted dimensions, disabled reasons, and warnings.

Do not include these images in logs. They may contain a real person and follow the recording's consent/storage policy.

## Tests and completion gate

Fixtures: neutral stance, one bent arm/open hand, one lifted/turned foot, right-arm-raised handedness, hand partly occluded, and a deliberate background-depth discontinuity.

Required tests:

- Model-index maps have expected count and unique canonical names.
- Crop→full-image mapping is reversible within pixel tolerance.
- Left/right association survives front/back views and asymmetric pose.
- Depth neighborhood excludes background across a discontinuity.
- Triangulation rejects parallel rays and high residuals.
- Prior registration never treats hip/hand-centered coordinates as stage coordinates.
- OBB construction yields orthonormal axes or disables cleanly.
- Missing data never emits `(0,0,0)` as a valid landmark.
- Capsule/OBB fit excludes visible gaps and reports default-vs-observed source.
- Python ray/overlap golden cases match Minecraft Java implementations.

Acceptance uses independently annotated/measured targets, not only self-consistency. Record median/worst hand and foot error and the failing body part. Widening a collider does not improve physical localization accuracy.
